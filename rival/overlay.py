"""Draw what the rival model counted on a test plan, so a person can check the count at a glance.

    python rival/overlay.py answers/rival-r3.jsonl colorful-11397 window door --out rival/out
Needs Pillow and the dataset image (junction-dataset-preview/data/cubicasa5k/<name>.png).
"""
import json, os, sys
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLORS = {"window": (0, 120, 255), "door": (230, 60, 0), "toilet": (150, 0, 200), "sink": (0, 160, 90)}


def main():
    answers, name = sys.argv[1], sys.argv[2]
    kinds = [a for a in sys.argv[3:] if not a.startswith("--") and a != "rival/out"] or ["window", "door"]
    out_dir = os.path.join(ROOT, "rival", "out")
    os.makedirs(out_dir, exist_ok=True)
    Q = [json.loads(l) for l in open(os.path.join(ROOT, "eval", "test_scans.jsonl"))]
    A = {r["id"]: r for r in (json.loads(l) for l in open(answers))}
    im = Image.open(os.path.join(ROOT, "junction-dataset-preview", "data", "cubicasa5k", name + ".png")).convert("RGB")
    k = max(im.size) / 1280          # evidence points are in the 1280-pixel working image, centred on a square canvas
    w, h = im.size[0] / k, im.size[1] / k
    ox, oy = (1280 - w) // 2, (1280 - h) // 2
    d = ImageDraw.Draw(im)
    r = max(6, int(9 * k))
    lines = []
    for q in Q:
        if os.path.basename(q["path"])[:-4] != name or q["lang"] != "en" or q["kind"] not in kinds:
            continue
        a = A.get(q["id"])
        if not a:
            continue
        col = COLORS.get(q["kind"], (200, 0, 0))
        for n, (x, y, v) in enumerate(a.get("evidence", []), 1):
            cx, cy = (x - ox) * k, (y - oy) * k
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=max(2, int(2 * k)))
            d.text((cx + r, cy - r), str(n), fill=col)
        lines.append(f"{q['kind']}: counted {a['answer']} (confidence {a['confidence']:.0%}), answer key {q['answer']}")
    path = os.path.join(out_dir, f"{name}.png")
    im.save(path)
    print("\n".join(lines))
    print(path)


if __name__ == "__main__":
    main()
