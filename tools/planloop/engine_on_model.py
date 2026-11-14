"""A frontier model's rebuilt plan (model.json, see plan_tool.py) -> DrawQA sheet -> our engine answers the questions.

    python engine_on_model.py <image> <model.json> <KEY.json> [<ENGINE.json>] [--sheet out.json]     (from the repo root)

The sheet is built with the converter's own add_elements (as the detector's output is), rooms keep their polygons, and
the scale is the model's, or else the converter's door-width estimate. solve() then runs unchanged on that sheet.
"""
import json, os, sys

sys.path.insert(0, os.getcwd())
from PIL import Image
from drawqa import convert, solve as S

ALL_CLASSES = ["door", "window", "wc", "sink", "bathtub", "shower", "sauna_stove", "sauna_bench", "bedroom", "kitchen", "bathroom",
               "wc_room", "balcony", "sauna_room", "floor", "room", "living_room", "dining", "entry", "storage", "utility",
               "dressing_room", "terrace", "garage", "other_room"]


def poly_area(p):
    return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))) / 2


def to_sheet(image, m):
    w, h = Image.open(image).size
    sheet = dict(source=os.path.abspath(image), source_type="raster", kind="image", units="px", y_flip=False, page=0,
                 width_px=w, height_px=h, format="drawqa-sheet/1", texts=[], rooms=[], render=dict(type="image"),
                 model="frontier model, render-and-compare", detector_classes=sorted(ALL_CLASSES),
                 detector_fit=dict(level="ok", note=""), scale=dict(m_per_px=None, source="no scale"))
    dets, room_src = [], {}
    for e in m.get("doors", []):
        dets.append(dict(cls="door", bbox=e["bbox"], conf=1.0))
    for e in m.get("windows", []):
        dets.append(dict(cls="window", bbox=e["bbox"], conf=1.0))
    for e in m.get("fixtures", []):
        dets.append(dict(cls=e["type"], bbox=e["bbox"], conf=1.0))
    for fl in m.get("floors", []) if isinstance(m.get("floors"), list) else []:
        dets.append(dict(cls="floor", bbox=fl["bbox"], conf=1.0))
    for r in m.get("rooms", []):
        p = r.get("polygon") or []
        if len(p) < 3 or r.get("type") not in convert.ROOM_TYPES:
            continue
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        room_src[len(dets)] = r
        dets.append(dict(cls=r["type"], bbox=[min(xs), min(ys), max(xs), max(ys)], conf=1.0))
    convert.add_elements(sheet, dets, found_by="frontier model")
    for e in sheet["elements"]:
        e["found_by"] = "frontier model"
    if m.get("m_per_px"):
        sheet["scale"] = dict(m_per_px=m["m_per_px"], estimated=True, source=f"frontier model: {m.get('scale_source') or 'scale given'}")
    else:
        sheet["scale"] = convert.estimate_scale_from_doors(sheet) or sheet["scale"]
    k = sheet["scale"].get("m_per_px")
    for room in sheet["rooms"]:  # ids are f"{cls}{index in dets}"
        i = int(room["id"][len(room["cls"]):])
        r = room_src.get(i)
        if r:
            room.update(polygon=r["polygon"], area_px=poly_area(r["polygon"]), label=r.get("name"), name=r.get("name"),
                        area_m2=round(poly_area(r["polygon"]) * k * k, 2) if k else None,
                        area_source="outline drawn by a frontier model" if k else None)
            if r.get("name"):
                xs, ys = [q[0] for q in r["polygon"]], [q[1] for q in r["polygon"]]
                cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
                sheet["texts"].append(dict(id=f"t{len(sheet['texts'])}", found_by="frontier model", text=r["name"], bbox=[cx - 10, cy - 6, cx + 10, cy + 6]))
    return sheet


def main():
    args = sys.argv[1:]
    out_sheet = args[args.index("--sheet") + 1] if "--sheet" in args else None
    args = [a for i, a in enumerate(args) if a != "--sheet" and (i == 0 or args[i - 1] != "--sheet")]
    image, model, key = args[:3]
    engine = {e["question"]: e["ours"] for e in json.load(open(args[3]))} if len(args) > 3 else {}
    sheet = to_sheet(image, json.load(open(model)))
    if out_sheet:
        json.dump(sheet, open(out_sheet, "w"), indent=1)
    S.load_sheet = lambda p, generic=False: sheet  # the frontier model's sheet stands in for the converter's
    rows, ok_m, ok_e = [], 0, 0
    for q in json.load(open(key)):
        r = S.solve(image, q["question"])
        a = r.get("answer")
        good = a == q["answer"] if q["type"] == "count" else (isinstance(a, (int, float)) and abs(a - q["answer"]) <= 0.02 * abs(q["answer"]))
        e = engine.get(q["question"])
        ok_m += good
        ok_e += e == q["answer"]
        rows.append(dict(question=q["question"][:70], key=q["answer"], frontier_sheet=a, detector=e, tier=(r.get("honesty") or {}).get("tier")))
        print(f"{'OK ' if good else 'X  '} key={q['answer']!s:5} frontier-sheet={a!s:5} detector={e!s:5}  {q['question'][:70]}")
    print(f"\nour engine on the frontier model's sheet: {ok_m}/{len(rows)}   on the detector's sheet: {ok_e}/{len(rows)}"
          f"   scale: {sheet['scale'].get('source')}")


if __name__ == "__main__":
    main()
