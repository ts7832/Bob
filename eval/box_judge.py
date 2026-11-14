"""Box-level judge: Aleks's detector proposes boxes, the rival model's map is asked about each one, a small model decides
which boxes to count. Windows and doors on the test scans.

    python eval/box_judge.py data/predictions/yolo_c26l_tta.jsonl answers/rival-r3.peaks.json labels.json answers/out.jsonl [--base answers/drawqa-c26l.jsonl]

For every box: the detector's confidence, the strongest peak of the rival's map inside the box, and the box's size.
For every strong rival peak outside all boxes: a candidate the detector missed. A logistic model gives each candidate
the probability that it is a real object; the count is the number of candidates above 50%.
Cross-fitted: plans are split in 5 groups and each group is decided by a model fitted on the other four.
labels.json (true positions, from rival/modal_rival.py prep) is used for fitting only. Standard library only.
"""
import json, math, os, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from calibrate import fit, predict  # noqa: E402

KIND = {k: k for k in (sys.argv[sys.argv.index("--kinds") + 1].split(",") if "--kinds" in sys.argv else ["window", "door"])}
SIZE = 1280


def logit(c):
    c = min(max(c, 0.01), 0.99)
    return math.log(c / (1 - c))


def candidates(boxes, peaks, truth, w, h):
    """-> [(features, is_real, box)] for one plan and one kind. All coordinates in original pixels."""
    k = SIZE / max(w, h)
    ox, oy = (SIZE - round(w * k)) // 2, (SIZE - round(h * k)) // 2
    P = [((x - ox) / k, (y - oy) / k, v) for x, y, v in peaks]
    T = [(x / k, y / k) for x, y in truth]
    used_t, used_p, out = set(), set(), []
    for b in sorted(boxes, key=lambda b: -b["conf"]):
        x0, y0, x1, y1 = b["bbox"]
        m = 3 / k
        inside = [i for i, (px, py, _) in enumerate(P) if x0 - m <= px <= x1 + m and y0 - m <= py <= y1 + m]
        peak = max((P[i][2] for i in inside), default=0.0)
        used_p.update(inside)
        hit = next((i for i, (tx, ty) in enumerate(T) if i not in used_t and x0 - m <= tx <= x1 + m and y0 - m <= ty <= y1 + m), None)
        if hit is not None:
            used_t.add(hit)
        long_side, short_side = max(x1 - x0, y1 - y0) * k, min(x1 - x0, y1 - y0) * k
        out.append(([1.0, logit(b["conf"]), min(peak, 8) / 8, 1.0 if peak == 0 else 0.0, min(long_side, 200) / 200, min(short_side, 60) / 60,
                     1.0 if len(inside) > 1 else 0.0, 0.0, 0.0], 1.0 if hit is not None else 0.0, [x0, y0, x1, y1]))
    for i, (px, py, v) in enumerate(P):
        if i in used_p or v < 1.5:
            continue
        r = 6 / k
        hit = next((j for j, (tx, ty) in enumerate(T) if j not in used_t and abs(tx - px) <= r and abs(ty - py) <= r), None)
        if hit is not None:
            used_t.add(hit)
        out.append(([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, min(v, 8) / 8], 1.0 if hit is not None else 0.0, [px - 8 / k, py - 8 / k, px + 8 / k, py + 8 / k]))
    return out


def main():
    f_boxes, f_peaks, f_labels, out_path = sys.argv[1:5]
    base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else None
    boxes = {json.loads(l)["path"][:-4]: json.loads(l)["boxes"] for l in open(f_boxes)}
    peaks = json.load(open(f_peaks))
    labels = json.load(open(f_labels))
    Q = [json.loads(l) for l in open(os.path.join(HERE, "test_scans.jsonl"))]
    data = {}
    for name in boxes:
        lab = labels.get(name)
        if lab is None or name not in peaks:
            continue
        for kd in KIND:
            data[(name, kd)] = candidates([b for b in boxes[name] if b["cls"] == kd and b["conf"] >= 0.1], peaks[name][kd], lab["points"][kd], lab["w"], lab["h"])
    fold = lambda name: zlib.crc32(name.encode()) % 5
    count = {}
    for kd in KIND:
        for f in range(5):
            X = [x for (n, k), c in data.items() if k == kd and fold(n) != f for x, _, _ in c]
            y = [t for (n, k), c in data.items() if k == kd and fold(n) != f for _, t, _ in c]
            w = fit(X, y, steps=300)
            for (n, k), c in data.items():
                if k == kd and fold(n) == f:
                    ps = [predict(w, x) for x, _, _ in c]
                    doubt = sum(1 for p in ps if 0.2 < p < 0.8)
                    count[(n, kd)] = (sum(1 for p in ps if p >= 0.5), doubt, ps)
    B = {r["id"]: r for r in (json.loads(l) for l in open(base))} if base else {}
    n_new = 0
    with open(out_path, "w") as out:
        for q in Q:
            name = os.path.basename(q["path"])[:-4]
            if (name, q["kind"]) in count:
                k, doubt, ps = count[(name, q["kind"])]
                conf = 1.0
                for p in ps:
                    conf *= max(p, 1 - p)          # chance that every single keep / drop decision is right
                out.write(json.dumps(dict(id=q["id"], answer=k, confidence=round(conf, 4), tier="detected",
                                          method="box judge: detector boxes checked against the rival map", cost_usd=0.0)) + "\n")
                n_new += 1
            elif q["id"] in B:
                out.write(json.dumps(B[q["id"]]) + "\n")
    print(f"{n_new} window and door answers from the box judge, the rest from {base} -> {out_path}")


if __name__ == "__main__":
    main()
