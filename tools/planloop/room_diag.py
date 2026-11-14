"""Where a room layer goes wrong, per plan (evaluator only; agents never see this):

    python room_diag.py <layer_dir> [plan ...]

Prints every GT room that was missed (best IoU < 0.5) with its best model match, every extra model room (matching no
GT room at IoU >= 0.3) with what GT it overlaps most, and every type mismatch.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import room_eval as R

d = sys.argv[1].rstrip("/")
plans = sys.argv[2:] or sorted(os.listdir(d))
for plan in plans:
    f = next((x for x in (f"{d}/{plan}/model_final.json", f"{d}/{plan}/start_model.json") if os.path.exists(x)), None)
    if not f or not os.path.isdir(f"{R.P}/gt/{plan}"):
        continue
    gt = [(g, R.poly(g["poly"])) for g in R.gt_rooms(plan)]
    gt = [(g, a) for g, a in gt if a is not None]
    md = [(r, R.poly(r["polygon"])) for r in json.load(open(f)).get("rooms", []) if len(r.get("polygon") or []) >= 3]
    md = [(r, b) for r, b in md if b is not None]
    pairs = sorted(((a.intersection(b).area / a.union(b).area, i, j) for i, (_, a) in enumerate(gt) for j, (_, b) in enumerate(md)
                    if a.intersection(b).area > 0), reverse=True)
    gi, mj, match = set(), set(), {}
    for iou, i, j in pairs:
        if i in gi or j in mj or iou < 0.3:
            continue
        gi.add(i); mj.add(j); match[i] = (j, iou)
    lines = []
    for i, (g, a) in enumerate(gt):
        j, iou = match.get(i, (None, 0.0))
        if iou < 0.5:
            best = max(((a.intersection(b).area / a.union(b).area, r) for r, b in md), key=lambda t: t[0], default=(0, {}))
            lines.append(f"  MISSED GT {g['type']!s:12} '{g['label']}' area {a.area:6.0f}  best IoU {best[0]:.2f} -> {best[1].get('id')} '{best[1].get('name')}' {best[1].get('type')}")
        elif g["type"] not in (None, "other_room"):
            t = md[j][0].get("type")
            if not (t == g["type"] or R.SAME.get(t) == g["type"] or R.SAME.get(g["type"]) == t):
                lines.append(f"  TYPE     GT {g['type']!s:12} '{g['label']}'  model {md[j][0].get('id')} '{md[j][0].get('name')}' {t}")
    for j, (r, b) in enumerate(md):
        if j in mj:
            continue
        cov = max(((b.intersection(a).area / b.area, g) for g, a in gt), key=lambda t: t[0], default=(0, {}))
        lines.append(f"  EXTRA    {r.get('id')} '{r.get('name')}' {r.get('type')} area {b.area:6.0f}  {cov[0]:.0%} inside GT {cov[1].get('type')} '{cov[1].get('label')}'")
    if lines:
        print(plan)
        print("\n".join(lines))
