"""Several engines, one answer: a learned chooser.

    python eval/choose.py OUT.jsonl A.jsonl B.jsonl C.jsonl ...        (needs numpy)

Every distinct answer the engines give to a question is a candidate. A logistic model scores each candidate from:
which engines voted for it (with a separate weight per engine and question kind, so it learns whom to trust for
what), their confidences, the share of engines agreeing, and the size of the count. The best-scored candidate is the
answer and its score is the confidence. Cross-fitted: plans are split in 5 groups, each decided by a model fitted on
the other four. An engine may answer only some questions (e.g. a window specialist).
"""
import json, os, sys, zlib
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ["balcony", "bathroom", "bedroom", "door", "floor", "kitchen", "sauna", "shower_bath", "sink", "toilet", "wc_room", "window"]


def fit(X, y, steps=1500, lr=1.0, l2=3e-3):
    w = np.zeros(X.shape[1])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-X @ w))
        w -= lr * (X.T @ (p - y) / len(y) + l2 * w)
    return w


def main():
    out_path, files = sys.argv[1], sys.argv[2:]
    Q = {q["id"]: q for q in (json.loads(l) for l in open(os.path.join(HERE, "test_scans.jsonl")))}
    E = [{r["id"]: r for r in (json.loads(l) for l in open(f) if l.strip())} for f in files]
    ne, nk = len(E), len(KINDS)
    rows, meta = [], []
    for i, q in Q.items():
        votes = [(e, E[e][i]) for e in range(ne) if i in E[e] and E[e][i].get("answer") is not None]
        if not votes:
            continue
        ki = KINDS.index(q["kind"])
        for a in sorted({float(r["answer"]) for _, r in votes}):
            x = np.zeros(3 + ne * 2 + ne * nk + nk)
            mine = [(e, r) for e, r in votes if float(r["answer"]) == a]
            x[0], x[1], x[2] = 1.0, len(mine) / len(votes), np.log1p(max(a, 0)) / 3
            for e, r in mine:
                c = min(max(r.get("confidence") or 0.5, 0.02), 0.98)
                x[3 + e] = 1.0
                x[3 + ne + e] = np.log(c / (1 - c)) / 4
                x[3 + 2 * ne + e * nk + ki] = 1.0
            x[3 + 2 * ne + ne * nk + ki] = 1.0
            rows.append(x)
            meta.append((i, a, zlib.crc32(q["path"].encode()) % 5, 1.0 if a == float(q["answer"]) else 0.0, mine[0][1]))
    X, y = np.array(rows), np.array([m[3] for m in meta])
    fold = np.array([m[2] for m in meta])
    score = np.zeros(len(y))
    for f in range(5):
        w = fit(X[fold != f], y[fold != f])
        score[fold == f] = 1 / (1 + np.exp(-X[fold == f] @ w))
    best = {}
    for (i, a, _, _, r), s in zip(meta, score):
        if i not in best or s > best[i][1]:
            best[i] = (a, s, r)
    with open(out_path, "w") as out:
        for i, (a, s, r) in best.items():
            out.write(json.dumps(dict(id=i, answer=int(a) if a == int(a) else a, confidence=round(float(s), 4), tier=r.get("tier", "detected"),
                                      method=f"chosen among {ne} engines", cost_usd=0.0)) + "\n")
    print(f"{len(best)} questions decided from {ne} engines -> {out_path}")


if __name__ == "__main__":
    main()
