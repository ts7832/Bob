"""Deterministic queries over a sheet graph. The LLM never counts or adds; this module does."""
import math, re

ROOM_WORDS = {"room": "general room", "living_room": "living room", "dining": "dining area", "entry": "entrance / hall",
              "storage": "storage / closet", "utility": "utility / technical room", "dressing_room": "dressing room",
              "other_room": "room with a combined or unusual label", "wc_room": "WC room", "sauna_room": "sauna room"}
FIXTURE_ALIASES = {"sink": {"washbasin", "sink"}, "shower+bathtub": {"shower", "bathtub"}}


def _shape(sheet, el, label=None):
    flip = -1 if sheet.get("y_flip") else 1
    if el.get("polygon"):
        return dict(id=el["id"], shape="polygon", pts=[[x, flip * y] for x, y in el["polygon"]], label=label)
    x0, y0, x1, y1 = el["bbox"]
    ys = sorted([flip * y0, flip * y1])
    return dict(id=el["id"], shape="box", bbox=[x0, ys[0], x1, ys[1]], label=label)


def _count(sheet, els, what):
    ev = [_shape(sheet, e, str(i + 1)) for i, e in enumerate(els)]
    if not els:  # a zero must say what was looked for: "0" reads as "there are none"
        looked = {"door": "door drawn with a leaf (plain openings without a leaf are not counted)",
                  "window": "window symbol"}.get(what, f"{what} symbol")
        how = ("the detector marked" if sheet.get("source_type") == "raster" or sheet.get("model") == "element detector" else "the file contains")
        return dict(answer=0, unit="count", evidence=ev, elements_used=els,
                    explanation=f"0: no {looked} was found among what {how} on this drawing. That is not proof that none exists.")
    return dict(answer=len(els), unit="count", evidence=ev, elements_used=els,
                explanation=f"Counted {len(els)} {what} element(s); each one is numbered on the drawing.")


# How close two symbols in a row must be to read as ONE opening, as a share of their short side (the wall thickness for
# a window box, the leaf width for a door box). Chosen by hand, not tuned on any answer key: a count is a convention.
TOUCH_GAP = {"window": 0.5, "door": 0.15}


def counting_readings(els, cls):
    """The same elements counted under the other common rules, so an answer can say how much depends on the rule.

    Drawing sets disagree on what "one window" or "one door" is: a long window with mullions is one opening or
    several panes; a double door is one door or two leaves; CAD files often carry two rectangles for one door.
    The answer always uses rule 1 (what the dataset's answer key counts). Rules 2 and 3 are reported next to it."""
    from .postprocess import merge_row
    boxes = [(e["bbox"], e.get("conf") or 1.0) for e in els if e.get("bbox")]
    if len(boxes) != len(els):
        return None
    return [dict(rule="each separately drawn symbol counts once", count=len(els), used=True),
            dict(rule="symbols drawn on top of each other count once", count=len(merge_row(boxes, None)), used=False),
            dict(rule=f"symbols that touch in a row along one wall count as one {cls} (double leaves, window bands)",
                 count=len(merge_row(boxes, TOUCH_GAP.get(cls, 0.15))), used=False)]


def _count_openings(sheet, els, cls):
    arcs = sheet.get("door_arcs") if cls == "door" else None
    fit = sheet.get("detector_fit") or {}
    if arcs and arcs.get("count") and (not els or fit.get("level") in ("weak", "none") or fit.get("missing") == "door"):
        # the detector does not know this drawing's door symbols, but the PDF's own swing arcs say where the doors are
        ev = [dict(id=f"arc{i}", shape="box", bbox=d["bbox"], label=str(i + 1)) for i, d in enumerate(arcs["doors"])]
        dbl = sum(1 for d in arcs["doors"] if d["double"])
        return dict(answer=arcs["count"], unit="count", evidence=ev, door_arcs=True,
                    explanation=f"Counted {arcs['count']} doors from their swing arcs in the PDF's own linework (each numbered on the "
                                f"drawing; {dbl} double door(s) count once; {arcs['swings']} swings in all)."
                                + (f" The detector found {len(els)} door symbol(s) here: it does not recognise this drawing's style."
                                   if sheet.get("model") == "element detector" else " The symbol detector was not run on this sheet."))
    res = _count(sheet, els, cls)
    if arcs and arcs.get("count") is not None:  # the PDF's swing arcs as a second, independent count
        res["explanation"] += (f" Door swings drawn in the PDF itself: {arcs['count']} doors (double doors once; "
                               f"{arcs['swings']} swings), from exact geometry.")
    readings = counting_readings(els, cls)
    if readings:
        res["counting"] = readings
        others = [r for r in readings[1:] if r["count"] != res["answer"]]
        res["explanation"] += (" Counting rule: each separately drawn symbol counts once. " +
                               ("Under other rules: " + "; ".join(f"{r['count']} if {r['rule']}" for r in others) + "."
                                if others else "The count is the same if overlapping or touching symbols are merged."))
    return res


def _sum_areas(sheet, rooms, what):
    named = len(rooms)
    rooms = [r for r in rooms if r.get("area_m2") is not None]
    if not rooms:  # never answer 0 m2 for "nothing could be measured"
        why = ("the rooms on this drawing have no printed area and no measurable outline" if named else
               "no matching room with an area was found on this drawing")
        if sheet.get("source_type") == "raster":
            why = "this is an image: rooms are found as boxes by a detector, not as measurable outlines, and no scale is printed"
        return dict(answer=None, explanation=f"{what}: area cannot be determined; {why}.")
    total = round(sum(r["area_m2"] for r in rooms), 2)
    ev = [_shape(sheet, r, f"{r.get('number', '')} {r['area_m2']:.2f}") for r in rooms if r.get("polygon")]
    terms = " + ".join(f"{r['area_m2']:.2f}" for r in rooms)
    left_out = (f" {named - len(rooms)} of {named} matching room(s) have no outline and no printed area, so they are not in this sum:"
                f" the true value is larger." if named > len(rooms) else "")
    if sheet.get("source_type") == "raster":
        by = {}
        for r in rooms:
            s = r.get("area_source") or ""
            k = ("printed area that its outline contradicts" if "disagrees" in s else "printed area confirmed by the outline" if "agrees" in s
                 else "printed area with no outline to check" if s.startswith("printed") else "outline at the sheet's scale")
            by[k] = by.get(k, 0) + 1
        left_out += (" Rooms by source: " + ", ".join(f"{n} from {k}" for k, n in by.items())
                     + ". On an image only rooms that were found can be summed; a room with no outline and no printed area would be missing.")
    du = {r.get("drawing_unit") for r in rooms}
    if du == {"sqft"}:  # the sheet prints its areas in square feet: say them in its own unit (answer converted at the end)
        terms = " + ".join(f"{r['area_m2'] / 0.09290304:.0f}" for r in rooms)
        return dict(answer=total, unit="m2", evidence=ev, area_sources=[r.get("area_source") for r in rooms], elements_used=rooms,
                    drawing_unit="sqft", explanation=f"{what}: {terms} = {total / 0.09290304:.0f} SF, as printed.{left_out}")
    return dict(answer=total, unit="m2", evidence=ev, area_sources=[r.get("area_source") for r in rooms], elements_used=rooms,
                explanation=f"{what}: {terms} = {total:.2f} m².{left_out}{_merged_note(rooms)}")


ASSUMED_HEIGHT = 2.5  # m; a common room height. Used only when the question gives none, and always said in the answer


def _select_rooms(sheet, intent):
    """Rooms a free-form step names: printed names (labels), room types (classes), a room number, an apartment letter,
    or nothing = all indoor rooms. -> (rooms, description)"""
    rooms = sheet.get("rooms", [])
    labels = intent.get("labels") or ([intent["label"]] if intent.get("label") else [])
    classes = [("sauna_room" if c == "sauna" else c) for c in (intent.get("classes") or [])]
    if labels:
        want = {l.strip().lower() for l in labels}
        return [r for r in rooms if (r.get("name") or "").strip().lower() in want], "rooms labelled " + ", ".join(f"'{l}'" for l in labels)
    if classes:
        return [r for r in rooms if r.get("cls") in classes], " + ".join(ROOM_WORDS.get(c, c) for c in classes) + " rooms"
    if intent.get("number"):
        return [r for r in rooms if r.get("number") == intent["number"]], f"room {intent['number']}"
    if intent.get("unit"):
        return [r for r in rooms if r.get("unit") == intent["unit"] and not r.get("outdoor")], f"apartment {intent['unit']} (indoor rooms)"
    return [r for r in rooms if not r.get("outdoor")], "all indoor rooms"


def _outline_source(sheet):
    return {"raster": "outlines drawn by the segmentation model", "vector-pdf": "room outlines from the PDF's linework",
            "vector-dxf": "exact CAD geometry"}.get(sheet.get("source_type"), "room outlines")


def _m_per_unit(sheet):
    """Metres per drawing unit: the scale of an image or PDF page, or the CAD file's own unit (a DXF in metres = 1)."""
    mpp = (sheet.get("scale") or {}).get("m_per_px")
    return mpp or {"m": 1.0, "mm": 0.001, "cm": 0.01}.get(sheet.get("units"))


def _perimeters(sheet, intent):
    """-> (result dict for a failure, None) or (None, (total m, [(room, m)], rooms without outline, description))."""
    rooms, what = _select_rooms(sheet, intent)
    mpp = _m_per_unit(sheet)
    if not rooms:
        return dict(answer=None, explanation=f"No {what} found on this drawing."), None
    if not mpp:
        return dict(answer=None, explanation=f"Perimeter of {what} cannot be measured: this drawing has no scale to turn its outlines into metres."), None
    raster = sheet.get("source_type") == "raster"
    done, missing = [], []
    for r in rooms:
        pts = r.get("polygon")
        if not pts or len(pts) < 3 or (raster and r.get("outline_conf") is None):  # a detector box is not an outline
            missing.append(r)
            continue
        done.append((r, round(sum(math.dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))) * mpp, 2)))
    if not done:
        return dict(answer=None, explanation=f"Perimeter of {what} cannot be measured: none of the {len(rooms)} room(s) has an outline."), None
    return None, (round(sum(p for _, p in done), 2), done, missing, what)


def _merged_note(rooms):
    """Outlines that hold several printed room names probably span several rooms (the wall model merged them)."""
    big = [r for r in rooms if len(r.get("names") or []) > 1]
    if not big:
        return ""
    return (f" Warning: {len(big)} outline(s) hold several printed room names ("
            + "; ".join(" / ".join(r["names"][:4]) for r in big[:3])
            + "), so each probably spans more than one room and its measure includes neighbours.")


def _room_tag(r):
    return " ".join(str(x) for x in (r.get("number"), r.get("name") or ROOM_WORDS.get(r.get("cls"), r.get("cls"))) if x)


def _room_perimeter(sheet, intent):
    fail, ok = _perimeters(sheet, intent)
    if fail:
        return fail
    total, done, missing, what = ok
    left = (f" {len(missing)} of {len(done) + len(missing)} matching room(s) have no outline and are NOT in this sum: the true value is larger."
            if missing else "")
    return dict(answer=total, unit="m", evidence=[_shape(sheet, r, f"{p:.2f} m") for r, p in done], elements_used=[r for r, _ in done],
                area_sources=[_outline_source(sheet)],
                explanation=f"Perimeter of {what}: " + " + ".join(f"{p:.2f} ({_room_tag(r)})" for r, p in done) + f" = {total:.2f} m, "
                            f"measured along each room's outline (wall faces; door openings are not subtracted).{left}{_merged_note([r for r, _ in done])}")


def _height(intent):
    """A ceiling height the question gave ("2.7", "2.7 m", "270 cm") -> metres, or None."""
    s = str(intent.get("height") or "").replace(",", ".")
    m = re.search(r"\d+(\.\d+)?", s)
    if not m:
        return None
    h = float(m.group())
    return h / 100 if ("cm" in s or h > 20) else h / 1000 if "mm" in s else h


def _wall_area(sheet, intent):
    fail, ok = _perimeters(sheet, intent)
    if fail:
        return fail
    total, done, missing, what = ok
    h = _height(intent)
    assumed = h is None
    h = ASSUMED_HEIGHT if assumed else h
    area = round(total * h, 2)
    left = f" {len(missing)} matching room(s) have no outline and are NOT included: the true value is larger." if missing else ""
    hnote = (f"the room height is ASSUMED to be {h:g} m (a common room height; a floor plan does not show it): "
             f"give the real height for a better answer" if assumed else f"room height {h:g} m as given in the question")
    res = dict(answer=area, unit="m2", evidence=[_shape(sheet, r, f"{p:.2f} m") for r, p in done], elements_used=[r for r, _ in done],
               area_sources=[_outline_source(sheet)],
               explanation=f"Wall area of {what} = perimeter {total:.2f} m"
                           + (f" = {' + '.join(f'{p:.2f} m ({_room_tag(r)})' for r, p in done)}" if len(done) > 1 else "")
                           + f" × height {h:g} m = {area:.2f} m²; {hnote}. Door and window "
                           f"openings are not subtracted, and no waste allowance is added.{left}")
    if assumed:
        res["assumed"] = f"room height assumed to be {h:g} m"
    return res


def _opening_widths(sheet, intent):
    cls = intent["cls"]
    mpp = _m_per_unit(sheet)
    els = [o for o in sheet.get("openings", []) if o["cls"] == cls]
    if not els:
        return dict(answer=None, explanation=f"No {cls} was found on this drawing, so there is no width to give.")
    widths = []
    for o in els:
        w = o.get("width_m")
        if w is None and mpp and o.get("bbox"):  # long side of the symbol's box (the leaf for a door, the pane run for a window)
            x0, y0, x1, y1 = o["bbox"]
            w = round(max(abs(x1 - x0), abs(y1 - y0)) * mpp, 2)
        if w is not None:
            widths.append((o, w))
    if not widths:
        return dict(answer=None, explanation=f"Widths of the {cls}s cannot be measured: this drawing has no scale.")
    how = ("measured along the wall it sits in" if any(o.get("width_m") is not None for o, _ in widths) else "taken as the long side of its symbol")
    ws = sorted(w for _, w in widths)
    groups = {}
    for w in ws:  # grouped to 5 cm: symbol boxes scatter by a few centimetres
        g = round(w * 20) / 20
        groups[g] = groups.get(g, 0) + 1
    listing = ", ".join(f"{g:.2f} m × {n}" for g, n in sorted(groups.items()))
    pick = (intent.get("quantity") or "").lower()
    skipped = f" {len(els) - len(widths)} {cls}(s) could not be measured." if len(widths) < len(els) else ""
    base = (f"Widths of {len(widths)} {cls}s, each {how}, grouped to 5 cm: {listing}. "
            f"Narrowest {ws[0]:.2f} m, widest {ws[-1]:.2f} m, median {ws[len(ws) // 2]:.2f} m.{skipped}")
    ev = [_shape(sheet, o, f"{w:.2f} m") for o, w in widths]
    used = [o for o, _ in widths]
    if pick in ("largest", "smallest"):
        w = ws[-1] if pick == "largest" else ws[0]
        return dict(answer=w, unit="m", evidence=ev, elements_used=used, explanation=f"The {'widest' if pick == 'largest' else 'narrowest'} {cls} is {w:.2f} m. " + base)
    return dict(answer=listing, unit=None, evidence=ev, elements_used=used, explanation=base)


def _names_of(r):
    return {n.strip().lower() for n in (r.get("names") or ([r["name"]] if r.get("name") else [])) if n and n.strip()}


def _printed_label_count(sheet, label, labels=None):
    """An image whose printed names were read: count the names themselves (each printed name is one room). Several
    names ('Secondary Classroom', 'Secondary Classroom (Severe Needs)') are counted together."""
    from .convert import name_key
    want = {name_key(l) for l in (labels or [label])}
    label = ", ".join(labels) if labels else label
    hits = [n for n in sheet.get("printed_names", []) if name_key(n["label"]) in want]
    ev = [dict(id=f"name{i}", shape="box", bbox=n["bbox"], label=n["label"]) for i, n in enumerate(hits)]
    if not hits:
        return dict(answer=0, unit="count", evidence=[], read_names=True,
                    explanation=f"0: no room name '{label}' was read on this drawing (names were read by a vision model, "
                                f"which can misread a letter).")
    by = sheet.get("printed_names_model") or "a vision model"
    return dict(answer=len(hits), unit="count", evidence=ev, read_names=True,
                explanation=f"Counted {len(hits)} printed room name(s) '{label}', read by {by}; each one is boxed "
                            f"on the drawing for checking.")


def _name_check(sheet, cls, counted):
    """The detector's count of a room type, checked against the names printed on the drawing (when they were read):
    names that mean this type (MAKUUH, MH = bedroom) printed in rooms the detector did not give this type. Changes no
    answer; says what the names suggest, so a missed room is not silent."""
    allp = sheet.get("printed_names", [])
    names = [n for n in allp if n.get("meaning") == ("sauna" if cls == "sauna_room" else cls)]
    if not names:
        if len(allp) >= 5 and counted:  # the sheet names its rooms, and none of the names means this type
            return (f" Check: none of the {len(allp)} room names printed on the drawing means {ROOM_WORDS.get(cls, cls)}, so "
                    f"what the detector found may be another kind of room or object.")
        return ""
    rooms = {}
    for n in names:  # one room per outline (MH + a corrected ASKARTELU in one room = one), loose names one each
        rooms.setdefault(n["room"] or f"loose{id(n)}", []).append(n)
    target = {cls, "sauna_room" if cls == "sauna" else cls}
    missed = [ns for ns in rooms.values() if ns[0]["room_cls"] not in target]
    if not missed:
        return f" Check: the names printed on the drawing agree ({', '.join(n['label'] for ns in rooms.values() for n in ns)})."
    word = ROOM_WORDS.get(cls, cls)
    return (f" Check against the printed room names (read by a vision model): they show {len(rooms)} {word}(s) "
            f"({'; '.join(', '.join(n['label'] for n in ns) for ns in rooms.values())}); {len(missed)} of them "
            f"({'; '.join(', '.join(n['label'] for n in ns) for ns in missed)}) the detector did not mark as {word}, "
            f"so if the names are right the true count is {len(rooms)}.")


def _name_check_area(sheet, classes, res):
    """Area of rooms by type, checked against the printed names: rooms whose printed name means one of these types but
    which the detector did not type that way. Says what the total would be with them; does not change the answer."""
    want = {("sauna" if c == "sauna_room" else c) for c in classes}
    typed = set(classes) | {"sauna_room" for c in classes if c == "sauna"}
    by_id = {r.get("id"): r for r in sheet.get("rooms", [])}
    extra, loose = {}, []
    for n in sheet.get("printed_names", []):
        if n.get("meaning") in want and n["room_cls"] not in typed:
            if n["room"] is None:
                loose.append(n["label"])
            else:
                extra.setdefault(n["room"], []).append(n["label"])
    if not extra and not loose:
        return
    rooms = [(by_id[i], ns) for i, ns in extra.items() if i in by_id]
    with_area = [(r, ns) for r, ns in rooms if r.get("area_m2") is not None]
    add = round(sum(r["area_m2"] for r, _ in with_area), 2)
    msg = (f" Check against the printed room names (read by a vision model): {len(rooms) + len(loose)} more room(s) are named "
           f"as this type but were not typed so by the detector ({'; '.join(', '.join(ns) for _, ns in rooms) + ('; ' if rooms and loose else '') + '; '.join(loose)})")
    if with_area and res.get("answer") is not None:
        msg += f"; with them the total is {res['answer'] + add:.2f} m² ({' + '.join(f'{r['area_m2']:.2f} {ns[0]}' for r, ns in with_area)} more)"
    res["explanation"] += msg + "."


TEXT_TOOLS = ("count_tags", "find_text", "read_value", "pipe_size", "slab_areas")


def _text_tool(sheet, intent):
    """Questions answered from the drawing's own text (drawqa/tags.py, drawqa/slabs.py). The result is marked
    read_text, so its tier is "read" with the text's source (exact PDF text, or OCR of an image)."""
    it, texts = intent["intent"], sheet.get("texts") or []
    if not texts:
        return dict(answer=None, explanation="No text was read on this drawing, so there is nothing to look up.")
    try:
        if it == "slab_areas":
            from . import slabs
            img = sheet.get("source") if sheet.get("source_type") == "raster" else None  # traced on the image when there is one
            res = slabs.answer(texts, img=img, m_per_px=_m_per_unit(sheet))
            res.pop("detail", None)
            if isinstance(res.get("answer"), str):  # a listing that already says "m2" for each type
                res["unit"] = None
            if res.get("answer") is not None:  # callout words and dimensions are read; which region a callout covers is judged
                res.update(tier="estimated",
                           basis="slab types and dimension numbers are read from the sheet; the region each callout covers is "
                                 "assigned by where the callout sits or where its leader points, which is a judgement; "
                                 "areas come from the printed dimensions where they cover the region, else from the traced outline")
        else:
            from . import tags
            label = intent.get("label") or ""
            if it == "count_tags":
                res = tags.count(texts, label)
            elif it == "find_text" and (nm := re.fullmatch(r"\s*(?:general\s+)?notes?\s*(?:to\s+designer\s*)?(?:no\.?|number|#)?\s*(\d{1,2})\s*", label, re.I)):
                return _note(tags.lines(texts), int(nm.group(1)))
            elif it == "find_text":
                lines = tags.find(texts, label)
                if not lines:
                    return dict(answer=None, read_text=True, explanation=f"No text containing '{label}' was found on this drawing.")
                shown = lines[:6]
                res = dict(answer=" | ".join(l["text"] for l in shown), unit=None,
                           evidence=[dict(id=f"line{i}", shape="box", bbox=l["bbox"], label=str(i + 1)) for i, l in enumerate(shown)],
                           explanation=f"{len(lines)} text line(s) on the drawing contain '{label}'"
                                       + (f"; the first {len(shown)} are shown" if len(lines) > len(shown) else "") + ".")
            elif it == "read_value":
                from .convert import name_key
                want = name_key(re.sub(r"\s*\([^)]*\)\s*$", "", label))  # "FIRST FLOOR (DORMS)" as listed -> FIRST FLOOR
                kv = tags.key_values(texts)
                hit = [x for x in kv if name_key(x.get("key") or x.get("label") or "") == want] or \
                      [x for x in kv if want and want in name_key(x.get("key") or x.get("label") or "")]
                if not hit:
                    return dict(answer=None, read_text=True, explanation=f"No value labelled '{label}' was found on this drawing.")
                x = hit[0]
                unit = {"SF": "sqft", "SQFT": "sqft", "M2": "m2", "M²": "m2"}.get(str(x.get("unit") or "").upper().replace(" ", ""), x.get("unit"))
                printed = x.get("raw") or f"{x.get('label') or x.get('key')} = {x.get('value')} {x.get('unit') or ''}".strip()
                res = dict(answer=x.get("value"), unit=unit, evidence=[dict(id="value", shape="box", bbox=x["bbox"], label=printed)],
                           explanation=f"Printed on the drawing: \"{printed}\"."
                                       + (f" ({len(hit)} labels match; the first is used.)" if len(hit) > 1 else ""))
            else:  # pipe_size
                res = tags.size_of(texts, label)
                if not res:
                    return dict(answer=None, read_text=True, explanation=f"No size printed with '{label}' was found on this drawing.")
    except ImportError:
        return dict(answer=None, explanation=f"The tool for this question ({it}) is not installed yet.")
    res.setdefault("read_text", True)
    return res


def _note(lines, n):
    """Note n of a numbered notes list: the line that starts with "n." (or "n "), plus the lines under it in the same
    column until the next numbered line. Every notes list on the sheet that has a note n is given ("NOTES:",
    "NOTES TO DESIGNER:"), with its heading when one is found above it."""
    start = re.compile(rf"^\s*{n}\s*[.)]\s+\S")
    numbered = re.compile(r"^\s*\d{1,2}\s*[.)]\s+\S")
    found = []
    for i, l in enumerate(lines):
        if not start.match(l["text"]):
            continue
        h = max(1.0, l["bbox"][3] - l["bbox"][1])
        block, last = [l], l
        while True:  # the nearest line straight below, in the same column (the list is not in reading order)
            below = [m for m in lines if m is not last and m not in block and -h <= m["bbox"][0] - l["bbox"][0] <= 6 * h
                     and 0 <= m["bbox"][1] - last["bbox"][1] and m["bbox"][1] - last["bbox"][3] <= 1.2 * h]
            if not below:
                break
            m = min(below, key=lambda m: m["bbox"][1])
            if numbered.match(m["text"]):
                break
            block.append(m)
            last = m
        head = [m for m in lines[:i] if "NOTE" in m["text"].upper() and 0 < l["bbox"][1] - m["bbox"][3] < 40 * h
                and abs(m["bbox"][0] - l["bbox"][0]) < 10 * h]
        bb = [min(b["bbox"][0] for b in block), min(b["bbox"][1] for b in block), max(b["bbox"][2] for b in block), max(b["bbox"][3] for b in block)]
        found.append(dict(text=" ".join(b["text"] for b in block), bbox=bb, heading=head[-1]["text"] if head else None))
    if not found:
        return dict(answer=None, read_text=True, explanation=f"No numbered note {n} was found on this drawing.")
    shown = " | ".join((f"{f['heading']} " if f["heading"] else "") + f["text"] for f in found)
    return dict(answer=shown, unit=None, read_text=True,
                evidence=[dict(id=f"note{i}", shape="box", bbox=f["bbox"], label=f"note {n}") for i, f in enumerate(found)],
                explanation=f"Note {n} as printed on the drawing" + (f" ({len(found)} notes lists have a note {n})" if len(found) > 1 else "") + ".")


def _overlaps(a, b, pad=4):
    return bool(a and b) and not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


def execute(sheet, intent):
    it = intent["intent"]
    rooms = sheet.get("rooms", [])
    if it in TEXT_TOOLS:
        return _text_tool(sheet, intent)
    if it == "count_rooms_label" and sheet.get("printed_names"):
        return _printed_label_count(sheet, intent["label"], intent.get("labels"))
    if it == "opening_width":
        return _opening_widths(sheet, intent)
    if it == "room_perimeter":
        return _room_perimeter(sheet, intent)
    if it == "wall_area":
        return _wall_area(sheet, intent)
    if it == "count_openings":
        return _count_openings(sheet, [o for o in sheet.get("openings", []) if o["cls"] == intent["cls"]], intent["cls"])
    if it == "scale":
        sc = sheet.get("scale") or {}
        if sheet.get("source_type") == "vector-dxf":
            return dict(answer=None, explanation="This is a CAD file: its geometry is stored in real units, so it has no drawing scale.")
        if not sc.get("m_per_px") and not sc.get("denominator"):
            return dict(answer=None, explanation=f"Scale: {sc.get('source') or 'no scale found on this drawing'}.")
        den = sc.get("denominator")
        ans = f"1:{den:g}" if den else f"{sc['m_per_px'] * 1000:.1f} mm per pixel"
        return dict(answer=ans, unit=None, scale_estimated=bool(sc.get("estimated")),
                    explanation=f"Scale {ans}; source: {sc.get('source')}" + (f"; error {sc['length_error']}" if sc.get("length_error") else "")
                                + (f". Cross-check: {sc['cross_check']}" if sc.get("cross_check") else "") + ".")
    if it == "wall_length":
        walls = [w for w in sheet.get("walls", []) if w.get("length_m") is not None]
        if not walls:
            return dict(answer=None, explanation="Wall length cannot be determined: no walls with a length were found on this drawing"
                                                 + ("" if sheet.get("walls") else " (walls are only found on images, by the segmentation model)") + ".")
        total = round(sum(w["length_m"] for w in walls), 2)
        return dict(answer=total, unit="m", evidence=[_shape(sheet, w, None) for w in walls], elements_used=walls,
                    area_sources=["wall centre lines found by the segmentation model"],
                    explanation=f"Sum of the centre-line lengths of {len(walls)} wall segments = {total:.2f} m. Segments meeting at a corner each run to the corner, so corners are counted from both sides.")
    if it == "count_fixtures":
        want = FIXTURE_ALIASES.get(intent["cls"], {intent["cls"]})
        return _count(sheet, [f for f in sheet.get("fixtures", []) if f["cls"] in want], "/".join(sorted(want)))
    if it == "count_units":
        units = [u for u in sheet.get("units_list", []) if u.get("residential")]
        return _count(sheet, units, "apartment")
    if it == "count_rooms_label":
        want = set(intent.get("labels") or [intent["label"]])
        return _count(sheet, [r for r in rooms if r.get("name") in want], "room labelled " + ", ".join(f"'{l}'" for l in sorted(want)))
    if it == "count_rooms_class" and intent["cls"] == "sauna" and sheet["source_type"] == "raster":
        # answer-key rule: a sauna = a stove, or benches without a stove, or a room marked as sauna
        stoves = [f for f in sheet.get("fixtures", []) if f["cls"] == "sauna_stove"]
        benches = [f for f in sheet.get("fixtures", []) if f["cls"] == "sauna_bench"]
        srooms = [r for r in rooms if r.get("cls") == "sauna_room"]
        best = max([stoves, srooms, benches[:1]], key=len)
        res = _count(sheet, best, "sauna (stove, sauna room or benches)")
        res["explanation"] += _name_check(sheet, "sauna", res["answer"])
        return res
    if it == "count_floors" and sheet.get("room_classes_available"):
        return _count(sheet, sheet.get("floors", []), "floor drawing")
    if it == "count_rooms_class" and sheet.get("room_classes_available"):
        known = sheet.get("detector_classes")
        if known and intent["cls"] not in known:  # never report 0 for something the detector cannot see
            return dict(answer=None, explanation=f"The detector in use was not trained to find '{intent['cls']}' rooms.")
        res = _count(sheet, [r for r in rooms if r.get("cls") == intent["cls"]], ROOM_WORDS.get(intent["cls"], intent["cls"]))
        res["explanation"] += _name_check(sheet, intent["cls"], res["answer"])
        return res
    if it == "count_rooms_class" and any(r.get("name") for r in rooms):
        # a drawing whose rooms carry printed names, not types (CAD, vector PDF): a name that is a known word for the
        # type counts (Schlafzimmer, Camera, MAKUUH = bedroom); otherwise say which names there are instead of guessing
        from .convert import name_meaning as means
        cls = intent["cls"]
        hit = [r for r in rooms if r.get("name") and means(r["name"]) == cls]
        if hit:
            res = _count(sheet, hit, f"room named as {ROOM_WORDS.get(cls, cls)} ({', '.join(sorted({r['name'] for r in hit}))})")
            res["read_names"] = True
            res["explanation"] += " Rooms are counted by their printed name; a fixed list says which names mean this room type."
            return res
        counts = {}
        for r in rooms:
            if r.get("name"):
                counts[r["name"]] = counts.get(r["name"], 0) + 1
        listing = ", ".join(f"{n} ×{c}" for n, c in sorted(counts.items(), key=lambda x: -x[1]))
        return dict(answer=None, explanation=f"Rooms on this drawing are known by their printed names, not by type ({listing}), and "
                                             f"none of these names is a word for '{ROOM_WORDS.get(cls, cls)}' that this system knows. "
                                             f"Ask about a name instead, e.g. 'how many rooms are labelled {next(iter(counts))}?'.")
    if it == "room_area":
        r = next((r for r in rooms if r.get("number") == intent["number"]), None)
        if r is None or r.get("area_m2") is None:
            return dict(answer=None, explanation=f"Room {intent['number']} not found on this sheet.")
        return dict(answer=r["area_m2"], unit="m2", evidence=[_shape(sheet, r, f"{r['number']} {r['area_m2']:.2f} m²")], area_sources=[r.get("area_source")],
                    explanation=f"Room {r['number']} ({r.get('name')}): {r['area_m2']:.2f} m², from {r['area_source']}.")
    if it == "label_area":  # one printed name (pattern route) or several (a language model's reading of "the toilets")
        labels = intent.get("labels") or [intent["label"]]
        want = {l.strip().lower() for l in labels}
        pick = [r for r in rooms if _names_of(r) & want and (not intent.get("unit") or r.get("unit") == intent["unit"])]
        res = _sum_areas(sheet, pick, "Rooms labelled " + ", ".join(f"'{l}'" for l in labels)
                         + (f" in unit {intent['unit']}" if intent.get("unit") else ""))
        if intent.get("unit") and any(r.get("unit_from") for r in pick):
            res["explanation"] = (res.get("explanation") or "") + " Rooms are assigned to the nearest unit tag on the sheet."
        loose = [n for n in sheet.get("printed_names", []) if n["room"] is None and n["label"].strip().lower() in want]
        if loose and res.get("answer") is not None:
            res["explanation"] += (f" {len(loose)} more printed name(s) ({', '.join(n['label'] for n in loose)}) lie in no room "
                                   f"outline, so their rooms are NOT in this sum: the true value is larger.")
        return res
    if it == "unit_area":
        rs = [r for r in rooms if r.get("unit") == intent["unit"] and not r.get("outdoor")]
        u = next((u for u in sheet.get("units_list", []) if u.get("label") == intent["unit"] and u.get("printed_total_m2")), None)
        if u:  # the sheet prints the unit's own total: that is the answer; its rooms are a cross-check
            tot = sum(r["area_m2"] for r in rs if r.get("area_m2") is not None)
            chk = (f" Its rooms read on the sheet add up to {tot / 0.09290304:.0f} SF" if u.get("drawing_unit") == "sqft" else
                   f" Its rooms read on the sheet add up to {tot:.2f} m²") + " (rooms are assigned to the nearest unit tag)." if rs else ""
            return dict(answer=u["printed_total_m2"], unit="m2", area_sources=["printed label"], drawing_unit=u.get("drawing_unit"),
                        evidence=[dict(id="unit_total", shape="box", bbox=u["bbox"], label=u["printed_text"])],
                        explanation=f"Unit {intent['unit']}: total printed on the sheet, \"{u['printed_text']}\".{chk}")
        return _sum_areas(sheet, rs, f"Apartment {intent['unit']} (indoor rooms)")
    if it == "count_rooms":  # every room the sheet names (with or without a printed area)
        named = [r for r in rooms if r.get("name") or r.get("area_m2") is not None]
        return _count(sheet, named, "named room")
    if it == "class_area":  # rooms known by type (found by the detector on an image), e.g. "area of the bedrooms"
        classes = intent.get("classes") or [intent["cls"]]
        classes = [("sauna_room" if c == "sauna" else c) for c in classes]
        if not sheet.get("room_classes_available"):
            # rooms known by printed names: a name that is a known word for the type counts ("BEDROOM", "1BD - BATH")
            from .convert import name_meaning
            want = {("sauna" if c == "sauna_room" else c) for c in classes}
            hit = [r for r in rooms if r.get("name") and name_meaning(r["name"]) in want]
            if hit:
                res = _sum_areas(sheet, hit, "Rooms named as " + " + ".join(ROOM_WORDS.get(c, c) for c in classes)
                                 + f" ({', '.join(sorted({r['name'] for r in hit}))})")
                res["explanation"] = (res.get("explanation") or "") + " Rooms are chosen by their printed name; a fixed list says which names mean this room type."
                return res
            return dict(answer=None, explanation="Rooms on this drawing are known by their printed names, not by type; ask for the area of rooms labelled with a name.")
        res = _sum_areas(sheet, [r for r in rooms if r.get("cls") in classes], " + ".join(ROOM_WORDS.get(c, c) for c in classes) + " rooms")
        _name_check_area(sheet, classes, res)
        return res
    if it == "floor_area":
        res = _sum_areas(sheet, [r for r in rooms if not r.get("outdoor")], "All indoor rooms")
        if sheet.get("unplaced_areas_m2") and res.get("answer") is not None:
            u = sheet["unplaced_areas_m2"]
            res["explanation"] += (f" Warning: {len(u)} more printed number(s) that look like room areas ({', '.join(f'{v:g}' for v in u)}; "
                                   f"{sum(u):g} m² together) could not be matched to a room of their own and are NOT in this sum, so the true total is probably larger.")
        if sheet.get("printed_total_m2") and res.get("answer") is not None:  # a total printed on the sheet: say how it compares
            t = sheet["printed_total_m2"]
            res["explanation"] += f" The sheet also prints a total of {t:g} m²; this sum differs from it by {abs(res['answer'] - t) / t * 100:.0f}%."
        return res
    return dict(answer=None, explanation=f"Not supported for this drawing type yet ({it}).")
