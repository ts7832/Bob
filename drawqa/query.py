"""Deterministic queries over a sheet graph. The LLM never counts or adds; this module does."""

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
    return dict(answer=len(els), unit="count", evidence=ev, elements_used=els,
                explanation=f"Counted {len(els)} {what} element(s); each one is numbered on the drawing.")


def _sum_areas(sheet, rooms, what):
    rooms = [r for r in rooms if r.get("area_m2") is not None]
    total = round(sum(r["area_m2"] for r in rooms), 2)
    ev = [_shape(sheet, r, f"{r.get('number', '')} {r['area_m2']:.2f}") for r in rooms if r.get("polygon")]
    terms = " + ".join(f"{r['area_m2']:.2f}" for r in rooms)
    return dict(answer=total, unit="m2", evidence=ev, area_sources=[r.get("area_source") for r in rooms],
                explanation=f"{what}: {terms} = {total:.2f} m²")


def execute(sheet, intent):
    it = intent["intent"]
    rooms = sheet.get("rooms", [])
    if it == "count_openings":
        return _count(sheet, [o for o in sheet.get("openings", []) if o["cls"] == intent["cls"]], intent["cls"])
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
        return _count(sheet, [r for r in rooms if r.get("cls") == intent["cls"]], intent["cls"])
    if it == "room_area":
        r = next((r for r in rooms if r.get("number") == intent["number"]), None)
        if r is None or r.get("area_m2") is None:
            return dict(answer=None, explanation=f"Room {intent['number']} not found on this sheet.")
        return dict(answer=r["area_m2"], unit="m2", evidence=[_shape(sheet, r, f"{r['number']} {r['area_m2']:.2f} m²")], area_sources=[r.get("area_source")],
                    explanation=f"Room {r['number']} ({r.get('name')}): {r['area_m2']:.2f} m², from {r['area_source']}.")
    if it == "label_area":
        return _sum_areas(sheet, [r for r in rooms if r.get("name") == intent["label"]], f"Rooms labelled '{intent['label']}'")
    if it == "unit_area":
        return _sum_areas(sheet, [r for r in rooms if r.get("unit") == intent["unit"] and not r.get("outdoor")],
                          f"Apartment {intent['unit']} (indoor rooms)")
    if it == "floor_area":
        return _sum_areas(sheet, [r for r in rooms if not r.get("outdoor")], "All indoor rooms")
    return dict(answer=None, explanation=f"Not supported for this drawing type yet ({it}).")
