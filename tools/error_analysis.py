"""Per-plan error table for scanned plans + overlay images of detections.

usage: .venv/bin/python tools/error_analysis.py [--overlay OUT_DIR] [--plans name1,name2]
"""
import argparse, collections, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from drawqa import scan

ROOT = os.path.join(os.path.dirname(__file__), "..", "junction-dataset-preview")
QMAP = {"window": "How many windows", "door": "How many doors", "wc": "How many toilet bowls",
        "sink": "How many sinks", "shower+bathtub": "How many showers and bathtubs"}
COLORS = {"window": (0, 160, 0), "door": (220, 0, 0), "wc": (0, 0, 220), "sink": (200, 0, 200),
          "bathtub": (0, 150, 200), "shower": (0, 150, 200), "sauna_bench": (150, 100, 0), "sauna_stove": (150, 100, 0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--overlay")
    ap.add_argument("--plans", default="")
    a = ap.parse_args()
    Q = [json.loads(l) for l in open(os.path.join(ROOT, "questions.jsonl")) if "cubicasa5k" in l]
    truth = collections.defaultdict(dict)
    for q in Q:
        for k, pre in QMAP.items():
            if q["question"].startswith(pre):
                truth[os.path.basename(q["path"])][k] = q["answer"]
    rows = []
    print(f"{'plan':42s}" + "".join(f"{k:>16s}" for k in QMAP))
    for name in sorted(scan.predictions()):
        sheet = scan.load(os.path.join(ROOT, "data/cubicasa5k", name))
        els = sheet["openings"] + sheet["fixtures"]
        c = collections.Counter(e["cls"] for e in els)
        c["shower+bathtub"] = c["shower"] + c["bathtub"]
        cells = []
        for k in QMAP:
            t = truth[name].get(k)
            cells.append("     -" if t is None else f"{c[k]:>5d} vs {t:<3d}{'  ' if c[k] == t else ' ✗'}")
            if t is not None:
                rows.append((name, k, c[k], t))
        print(f"{name[:42]:42s}" + "".join(f"{x:>16s}" for x in cells))
    print("\nper class: under-count / exact / over-count")
    for k in QMAP:
        r = [x for x in rows if x[1] == k]
        print(f"  {k:16s} under {sum(p < t for _, _, p, t in r):3d}  exact {sum(p == t for _, _, p, t in r):3d}  over {sum(p > t for _, _, p, t in r):3d}"
              f"   total pred {sum(p for *_, p, _ in r)} vs truth {sum(t for *_, t in r)}")
    if a.overlay:
        import cv2
        os.makedirs(a.overlay, exist_ok=True)
        for name in [p for p in a.plans.split(",") if p]:
            sheet = scan.load(os.path.join(ROOT, "data/cubicasa5k", name))
            img = cv2.imread(os.path.join(ROOT, "data/cubicasa5k", name))
            for e in sheet["openings"] + sheet["fixtures"]:
                x0, y0, x1, y1 = map(int, e["bbox"])
                col = COLORS.get(e["cls"], (120, 120, 120))
                cv2.rectangle(img, (x0, y0), (x1, y1), col, 2)
                cv2.putText(img, e["cls"][:3], (x0, max(10, y0 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
            cv2.imwrite(os.path.join(a.overlay, name), img)


if __name__ == "__main__":
    main()
