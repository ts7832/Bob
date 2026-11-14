"""Deterministic queries over a sheet graph. The LLM never counts or adds; this module does."""

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
    res = _count(sheet, els, cls)
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
    return dict(answer=total, unit="m2", evidence=ev, area_sources=[r.get("area_source") for r in rooms], elements_used=rooms,
                explanation=f"{what}: {terms} = {total:.2f} m².{left_out}")


def execute(sheet, intent):
    it = intent["intent"]
    rooms = sheet.get("rooms", [])
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
        return _count(sheet, [r for r in rooms if r.get("name") == intent["label"]], f"room labelled '{intent['label']}'")
    if it == "count_rooms_class" and intent["cls"] == "sauna" and sheet["source_type"] == "raster":
        # answer-key rule: a sauna = a stove, or benches without a stove, or a room marked as sauna
        stoves = [f for f in sheet.get("fixtures", []) if f["cls"] == "sauna_stove"]
        benches = [f for f in sheet.get("fixtures", []) if f["cls"] == "sauna_bench"]
        srooms = [r for r in rooms if r.get("cls") == "sauna_room"]
        best = max([stoves, srooms, benches[:1]], key=len)
        return _count(sheet, best, "sauna (stove, sauna room or benches)")
    if it == "count_floors" and sheet.get("room_classes_available"):
        return _count(sheet, sheet.get("floors", []), "floor drawing")
    if it == "count_rooms_class" and sheet.get("room_classes_available"):
        known = sheet.get("detector_classes")
        if known and intent["cls"] not in known:  # never report 0 for something the detector cannot see
            return dict(answer=None, explanation=f"The detector in use was not trained to find '{intent['cls']}' rooms.")
        return _count(sheet, [r for r in rooms if r.get("cls") == intent["cls"]], ROOM_WORDS.get(intent["cls"], intent["cls"]))
    if it == "room_area":
        r = next((r for r in rooms if r.get("number") == intent["number"]), None)
        if r is None or r.get("area_m2") is None:
            return dict(answer=None, explanation=f"Room {intent['number']} not found on this sheet.")
        return dict(answer=r["area_m2"], unit="m2", evidence=[_shape(sheet, r, f"{r['number']} {r['area_m2']:.2f} m²")], area_sources=[r.get("area_source")],
                    explanation=f"Room {r['number']} ({r.get('name')}): {r['area_m2']:.2f} m², from {r['area_source']}.")
    if it == "label_area":  # one printed name (pattern route) or several (a language model's reading of "the toilets")
        labels = intent.get("labels") or [intent["label"]]
        want = {l.strip().lower() for l in labels}
        return _sum_areas(sheet, [r for r in rooms if (r.get("name") or "").strip().lower() in want],
                          "Rooms labelled " + ", ".join(f"'{l}'" for l in labels))
    if it == "unit_area":
        return _sum_areas(sheet, [r for r in rooms if r.get("unit") == intent["unit"] and not r.get("outdoor")],
                          f"Apartment {intent['unit']} (indoor rooms)")
    if it == "class_area":  # rooms known by type (found by the detector on an image), e.g. "area of the bedrooms"
        classes = intent.get("classes") or [intent["cls"]]
        classes = [("sauna_room" if c == "sauna" else c) for c in classes]
        if not sheet.get("room_classes_available"):
            return dict(answer=None, explanation="Rooms on this drawing are known by their printed names, not by type; ask for the area of rooms labelled with a name.")
        return _sum_areas(sheet, [r for r in rooms if r.get("cls") in classes], " + ".join(ROOM_WORDS.get(c, c) for c in classes) + " rooms")
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
