"""How many of the test plans' doors a detector finds, by door kind (Swing, Slide, Zfold, RollUp, ...).

    .venv/bin/python tools/door_kind_recall.py data/predictions/yolo_e27_tta.jsonl [more.jsonl ...]

Uses data/eval/preview_doors.json (CubiCasa's own door labels of the 241 preview plans with their kind, from
`modal run tools/modal_train.py --stage preview_doors`; evaluation only) and each predictions file with its
.thresholds.json. A labelled door counts as found when a kept door box overlaps it.
"""
import collections, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from drawqa.postprocess import apply

ROOT = os.path.join(os.path.dirname(__file__), "..")
KEY = json.load(open(os.path.join(ROOT, "data", "eval", "preview_doors.json")))


def overlaps(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > 0 and min(a[3], b[3]) - max(a[1], b[1]) > 0


for f in sys.argv[1:]:
    P = {json.loads(l)["path"][:-4]: json.loads(l) for l in open(f)}
    t = json.load(open(f[:-6] + ".thresholds.json"))
    found, total, extra = collections.Counter(), collections.Counter(), 0
    for plan, doors in KEY.items():
        if plan not in P:
            continue
        ours = [d["bbox"] for d in apply([dict(cls=b["cls"], bbox=b["bbox"], conf=b["conf"]) for b in P[plan]["boxes"]],
                                         t["thresholds"], t.get("rules")) if d["cls"] == "door"]
        for kind, box in doors:
            if kind == "None":  # a doorway without a door leaf: not a door in the questions or in training
                continue
            total[kind] += 1
            found[kind] += any(overlaps(box, o) for o in ours)
        extra += sum(1 for o in ours if not any(overlaps(o, b) for k, b in doors if k != "None"))
    print(os.path.basename(f), "| door cutoff", t["thresholds"].get("door"))
    for k in sorted(total, key=lambda k: -total[k]):
        print(f"   {k:14s} found {found[k]:4d} / {total[k]:4d} = {100 * found[k] / total[k]:5.1f}%")
    rare = [k for k in total if k != "Swing"]
    print(f"   {'all but swing':14s} found {sum(found[k] for k in rare):4d} / {sum(total[k] for k in rare):4d}; door boxes on no labelled door: {extra}")
