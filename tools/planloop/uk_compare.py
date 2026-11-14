"""UK school sheet: each run's room layer against the earlier Opus from-scratch layer (stoa/uk, checked by hand: 79 rooms,
scale bar 0.008478 m/px, hall 244.4 m2) and against the sheet's own printed names.

    python uk_compare.py <folder> [<folder> ...]      each holds model_final.json
"""
import json, os, re, sys
from shapely.geometry import Polygon

P = os.path.dirname(os.path.abspath(__file__))
MPP = 0.008478  # scale bar, checked against 1:200 on A2
ref = [(r, Polygon(r["polygon"]).buffer(0)) for r in json.load(open(f"{P}/stoa/uk/model_final.json"))["rooms"] if len(r.get("polygon") or []) >= 3]
words = [w for w in json.load(open(f"{P}/stoa/uk/ocr_text.json")) if re.search(r"[A-Za-z]{3}", w["text"])]


def norm(s):
    return re.sub(r"[^a-z]", "", (s or "").lower())


for d in sys.argv[1:]:
    f = f"{d.rstrip('/')}/model_final.json"
    if not os.path.exists(f):
        print(os.path.basename(d.rstrip("/")), "no model_final.json"); continue
    m = json.load(open(f))
    rooms = [(r, Polygon(r["polygon"]).buffer(0)) for r in m.get("rooms", []) if len(r.get("polygon") or []) >= 3]
    matched, ious, same_name, aerr = 0, [], 0, []
    used = set()
    for r, a in ref:
        best = max(((a.intersection(b).area / a.union(b).area, j) for j, (_, b) in enumerate(rooms) if j not in used), default=(0, None))
        if best[0] >= 0.5:
            matched += 1; used.add(best[1]); ious.append(best[0])
            mr, mb = rooms[best[1]]
            same_name += norm(mr.get("name")) == norm(r.get("name"))
            aerr.append(abs(mb.area / a.area - 1))
    hall = [b.area * MPP ** 2 for r, b in rooms if "hall" in (r.get("name") or "").lower() and "multi" in (r.get("name") or "").lower()]
    cls = sum(1 for r, _ in rooms if "classroom" in (r.get("name") or "").lower())
    grp = sum(1 for r, _ in rooms if "group room" in (r.get("name") or "").lower())
    mpp = m.get("m_per_px")
    print(f"{os.path.basename(d.rstrip('/')):16s} rooms {len(rooms):3d} | match ref {matched}/{len(ref)} (IoU {sum(ious) / max(1, len(ious)):.2f}, "
          f"same name {same_name}, median area diff {sorted(aerr)[len(aerr) // 2] * 100 if aerr else 0:.1f}%) | extra {len(rooms) - len(used)} | "
          f"classrooms {cls} groups {grp} | hall {', '.join(f'{h:.1f}' for h in hall) or '-'} m2 | m_per_px {mpp} ({(mpp / MPP - 1) * 100 if mpp else 0:+.2f}%)")
