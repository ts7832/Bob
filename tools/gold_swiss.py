"""Gold questions with exact answers from Swiss CAD files: python tools/gold_swiss.py [--plans N] [--write]

The challenge setters said they will ask for floor areas, baseboard (skirting) lengths and counts of doors and windows.
The Swiss preview plans come as a PDF and a DXF of the same drawing; the DXF holds the exact geometry, so the answer
to each of those questions can be computed, and the question asked of the PDF alone (gold line `"generic": true`,
which makes tools/gold_eval.py call solve(..., generic=True): no DXF sibling, no dataset shortcuts).

Computed here with ezdxf + shapely, independently of drawqa/swiss.py:
  area       the room's ROOM_OUTLINE polygon (net, inside the walls)
  baseboard  the room's outline perimeter minus the width of every door opening in its walls; windows do not
             interrupt it (their sills are above the floor); outdoor rooms (balcony, loggia, terrace) have none
  doors      closed 5-point polylines on layer DOOR (one per opening; its long side is the door width)
  windows    4-point rectangles on layer WINDOW (each window is a rectangle plus two glass lines)

Checks (printed on every run): outline areas against the areas printed on the plans that print them; door and
window counts against the dataset's own labels. Only plans that print no areas are used, so the system has to
measure. Rooms whose printed area disagrees with their outline are never used.

Lines get status "exact" (computed from the CAD source, not typed by a person); gold_eval.py scores them like
"verified". --write appends new lines to gold/gold.jsonl (skipping questions already there); without it, a dry run.
"""
import argparse, collections, glob, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.path.join(REPO, "junction-dataset-preview")
GOLD = os.path.join(REPO, "gold", "gold.jsonl")

ROOM_NO = re.compile(r"^[A-Z]?\d*\.?\d+$")
AREA_TXT = re.compile(r"^([\d.]+)\s*m²$")
UNIT_LABEL = re.compile(r"^(App\.|Whg\.)\s*([A-Z])$")
OUTDOOR = re.compile(r"^(Balkon|Balcon|Balcone|Loggia|Terrasse|Terrazza|Sitzplatz)$", re.I)
TOUCH = 0.03  # metres: a door opening whose rectangle lies within this of a room outline is in that room's wall


def truth(dxf_path):
    import ezdxf
    from shapely.geometry import Point, Polygon

    def poly(e):
        P = Polygon([(x, y) for x, y, *_ in e.get_points()])
        return P if P.is_valid else P.buffer(0)

    msp = ezdxf.readfile(dxf_path).modelspace()
    L = collections.defaultdict(list)
    for e in msp:
        L[e.dxf.layer].append(e)
    outlines = [poly(e) for e in L["ROOM_OUTLINE"] if e.dxftype() == "LWPOLYLINE" and len(e) >= 3]
    texts = [(e.dxf.text.strip(), Point(e.dxf.insert.x, e.dxf.insert.y)) for e in L["ROOM_TEXT"] if e.dxftype() == "TEXT"]
    units = []
    for e in L["UNIT_OUTLINE"]:
        U = poly(e)
        lab = next((UNIT_LABEL.match(t).group(2) for t, p in texts if UNIT_LABEL.match(t) and U.contains(p)), None)
        units.append((lab, U))
    doors = []
    for e in L["DOOR"]:
        if e.dxftype() == "LWPOLYLINE" and len(e) == 5:
            r = poly(e)
            x0, y0, x1, y1 = r.bounds
            doors.append((r, max(x1 - x0, y1 - y0)))
    windows = [e for e in L["WINDOW"] if e.dxftype() == "LWPOLYLINE" and len(e) == 4]
    rooms = []
    for k, (t, p) in enumerate(texts):
        if not ROOM_NO.match(t):
            continue
        prev = texts[k - 1][0] if k else ""
        name = prev if prev and not re.search(r"\d", prev) and not UNIT_LABEL.match(prev) else None
        m = AREA_TXT.match(texts[k + 1][0]) if k + 1 < len(texts) else None
        cands = [o for o in outlines if o.contains(p)]
        if not cands:
            continue
        P = min(cands, key=lambda o: o.area)
        widths = [w for r, w in doors if r.distance(P.exterior) < TOUCH and r.intersection(P.buffer(TOUCH)).area > 0]
        outdoor = bool(name and OUTDOOR.match(name))
        rooms.append(dict(number=t, name=name, area=round(P.area, 2), printed=float(m.group(1)) if m else None,
                          perimeter=round(P.exterior.length, 3), door_widths=[round(w, 3) for w in widths],
                          baseboard=None if outdoor else round(P.exterior.length - sum(widths), 2),
                          unit=next((lab for lab, U in units if U.contains(p)), None), outdoor=outdoor))
    return dict(doors=len(doors), windows=len(windows), rooms=rooms, units=sorted(l for l, _ in units if l))


def checks(T):
    agree = bad = 0
    for t in T.values():
        for r in t["rooms"]:
            if r["printed"] is not None:
                ok = abs(r["area"] - r["printed"]) <= 0.02 * r["printed"]
                agree += ok
                bad += not ok
    Q = [json.loads(l) for l in open(os.path.join(ROOT, "questions.jsonl"), encoding="utf-8")]
    c = collections.Counter()
    for q in Q:
        if "/swiss_dwellings/" not in q["path"] or q["type"] != "count":
            continue
        stem = os.path.splitext(os.path.basename(q["path"]))[0]
        s = q["question"].lower()
        for kind, key in (("door", "doors"), ("window", "windows")):
            if s.startswith("how many") and kind in s and "room" not in s and stem in T:
                c[T[stem][key] == q["answer"]] += 1
    print(f"check: outline area within 2% of the printed area on {agree} rooms, off on {bad} (those rooms are not used)")
    print(f"check: door/window counts equal the dataset label on {c[True]} questions, differ on {c[False]}")
    if c[False]:
        raise SystemExit("door/window counts disagree with dataset labels: fix the rules before writing gold lines")


def questions(stem, t):
    path = f"data/swiss_dwellings/{stem}.pdf"
    usable = [r for r in t["rooms"] if r["name"] and r["printed"] is None]
    indoor = [r for r in usable if not r["outdoor"]]
    out = []

    def q(text, typ, unit, answer, tier, note):
        out.append(dict(path=path, question=text, type=typ, unit=unit, answer=answer, expected_tier=tier, note=note))

    if indoor:
        big = max(indoor, key=lambda r: r["area"])
        q(f"What is the net floor area of room {big['number']} ('{big['name']}') in m²?", "area", "m2", big["area"], "measured",
          f"ROOM_OUTLINE polygon of room {big['number']}; no area is printed on this plan")
        names = collections.Counter(r["name"] for r in indoor)
        name, n = names.most_common(1)[0]
        if n >= 2:
            tot = round(sum(r["area"] for r in indoor if r["name"] == name), 2)
            q(f"What is the total net floor area of all rooms labelled '{name}' on this floor, in m²?", "area", "m2", tot, "measured",
              f"sum of the {n} ROOM_OUTLINE polygons of rooms named {name}")
        simple = [r for r in indoor if 1 <= len(r["door_widths"]) <= 2 and r["area"] >= 6]
        if simple:
            r = max(simple, key=lambda r: r["area"])
            q(f"How many metres of baseboard (skirting) does room {r['number']} ('{r['name']}') need? Take the room's perimeter and leave out door openings.",
              "length", "m", r["baseboard"], "measured",
              f"perimeter {r['perimeter']} m minus door openings {r['door_widths']} m")
    for u in t["units"]:
        rs = [r for r in t["rooms"] if r["unit"] == u and not r["outdoor"] and r["baseboard"] is not None]
        if len(rs) >= 3:
            q(f"What is the total baseboard (skirting) length for apartment {u} in metres? Leave out door openings and outdoor spaces.",
              "length", "m", round(sum(r["baseboard"] for r in rs), 2), "measured",
              f"sum over the {len(rs)} indoor rooms of apartment {u}: perimeter minus door openings")
            break
    q("How many doors are shown on this floor plan?", "count", "count", t["doors"], "detected",
      "door openings on the DXF's DOOR layer (a double door is one opening)")
    q("How many windows are shown on this floor plan?", "count", "count", t["windows"], "detected",
      "window rectangles on the DXF's WINDOW layer")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", type=int, default=4, help="how many plans without printed areas to use")
    ap.add_argument("--write", action="store_true", help="append to gold/gold.jsonl")
    a = ap.parse_args()
    T = {os.path.splitext(os.path.basename(f))[0]: truth(f) for f in sorted(glob.glob(os.path.join(ROOT, "data", "swiss_dwellings", "*.dxf")))}
    checks(T)
    plans = [s for s, t in T.items() if t["rooms"] and all(r["printed"] is None for r in t["rooms"]) and len(t["units"]) >= 2]
    plans.sort(key=lambda s: (-len(T[s]["rooms"]), s))  # the busiest sheets first: closest to what judges hand over
    new = [x for s in plans[: a.plans] for x in questions(s, T[s])]

    existing = [json.loads(l) for l in open(GOLD, encoding="utf-8") if l.strip()]
    seen = {(r["path"], r["question"]) for r in existing}
    n = max(int(r["id"][1:]) for r in existing)
    rows = []
    for x in new:
        if (x["path"], x["question"]) in seen:
            continue
        n += 1
        rows.append(dict(id=f"g{n:03d}", path=x["path"], question=x["question"], type=x["type"], unit=x["unit"], answer=x["answer"],
                         expected_tier=x["expected_tier"], status="exact", proposed_answer=x["answer"],
                         proposed_by="tools/gold_swiss.py (computed from the DXF)", evidence_note=x["note"], origin="judge-style",
                         dataset_answer=None, verified_by="computed from the DXF, independent of drawqa/swiss.py", note="",
                         generic=True))
    for r in rows:
        print(f"{r['id']} {r['answer']!s:>8} {r['unit']:5s} {r['path'][-36:]}  {r['question'][:80]}")
    if a.write and rows:
        with open(GOLD, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"appended {len(rows)} lines to gold/gold.jsonl")
    elif not a.write:
        print(f"dry run: {len(rows)} new lines (pass --write to append)")


if __name__ == "__main__":
    main()
