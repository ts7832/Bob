"""The pattern router (drawqa/router.py) on the same test questions as the Jev-style router, for a fair comparison.

    MODAL_PROFILE=kokoro-63208 modal volume get bob-data /jev/router1/report.json /tmp/report.json
    python jev/compare_router.py /tmp/report.json            (from a checkout that has drawqa/)
"""
import json, sys

sys.path.insert(0, ".")
from drawqa import router  # noqa: E402


def meaning(i, c, u=None):
    if i in ("room_area", "ifc_room_area"): return "area:one_room"
    if i in ("floor_area", "ifc_storey_area"): return "area:whole_floor"
    if i == "unit_area": return "area:apartment"
    if i in ("label_area", "class_area"): return "area:rooms_of_a_type"
    if i in ("count_rooms_label", "count_rooms_class"): return "count:rooms_of_a_type"
    if i == "ifc_count" and c == "space": return "count:all_rooms"
    if i in ("count_openings", "ifc_count") and c in ("door", "window"): return "count:" + c
    if i == "count_fixtures" or (i == "ifc_count" and c == "sanitary"):
        return "count:fixtures:" + ("any" if c == "sanitary" else str(c).replace("shower+bathtub", "shower_or_bathtub"))
    if i == "ifc_count" and c in ("wall", "stair", "column", "beam"): return "count:" + c
    if i == "ifc_quantity": return "quantity:" + str(c)          # length/area/volume is decided by the unit asked, not the router
    if i == "count_units": return "count:apartments"
    if i == "count_floors": return "count:floors"
    return "unknown"


rep = json.load(open(sys.argv[1]))
rows = rep["test_rows"]
ok = unk = 0
for r in rows:
    p = router.parse(r["question"])
    m = meaning(p["intent"], p.get("cls"))
    want = r["label"]
    if m == "unknown":
        unk += 1
        ok += want == "other"                                    # saying "I can't read it" is right only for out-of-scope questions
    elif m.startswith("quantity:"):
        ok += want.split(":")[-1] == m.split(":")[-1] and want.split(":")[0] in ("length", "volume", "area")
    else:
        ok += m == want
print(f"pattern router on the same {len(rows)} test rewordings: {ok}/{len(rows)} = {100 * ok / len(rows):.1f}%  ({unk} not read at all)")
print("Jev-style router on them:", rep["test_rewordings"]["accuracy"], "| on the questions the pattern router cannot read:", rep["router_cannot_read"]["accuracy"])
