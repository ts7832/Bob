"""Honest confidence for an existing answer file: same answers, corrected confidence numbers.

    python eval/calibrate.py answers/drawqa-c26l.jsonl answers/drawqa-c26l-calibrated.jsonl [--model eval/calibration.json]

A small logistic model predicts "is this answer exactly right?" from the engine's own confidence, the question kind
and the size of the count. It never changes an answer, so accuracy cannot move; only the confidence shown does.

To keep the reported numbers honest, the plans are split in 5 groups and each group's confidences come from a model
fitted on the other four (a plan's English and Finnish questions stay together). The model written with --model is
fitted on all plans and is the one to use on new drawings. Standard library only.
"""
import argparse, json, math, os, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ["balcony", "bathroom", "bedroom", "door", "floor", "kitchen", "sauna", "shower_bath", "sink", "toilet", "wc_room", "window"]


def features(kind, conf, answer):
    c = min(max(conf if conf is not None else 0.5, 0.02), 0.98)
    x = [1.0, math.log(c / (1 - c)), math.log1p(max(answer or 0, 0)), 1.0 if (answer or 0) == 0 else 0.0]
    return x + [1.0 if kind == k else 0.0 for k in KINDS]


def fit(X, y, steps=400, lr=0.5, l2=1e-3):
    w = [0.0] * len(X[0])
    n = len(X)
    for _ in range(steps):
        g = [0.0] * len(w)
        for x, t in zip(X, y):
            p = 1 / (1 + math.exp(-sum(a * b for a, b in zip(w, x))))
            d = p - t
            for j, v in enumerate(x):
                if v:
                    g[j] += d * v
        w = [wj - lr * (gj / n + l2 * wj) for wj, gj in zip(w, g)]
    return w


def predict(w, x):
    return 1 / (1 + math.exp(-sum(a * b for a, b in zip(w, x))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("answers")
    ap.add_argument("out")
    ap.add_argument("--model")
    ap.add_argument("--abstain-below", type=float, help="also answer 'cannot determine' under this confidence")
    a = ap.parse_args()
    Q = {q["id"]: q for q in (json.loads(l) for l in open(os.path.join(HERE, "test_scans.jsonl")))}
    A = [json.loads(l) for l in open(a.answers) if l.strip()]
    rows = [r for r in A if r.get("answer") is not None and r["id"] in Q]
    X = [features(Q[r["id"]]["kind"], r.get("confidence"), r["answer"]) for r in rows]
    y = [1.0 if float(r["answer"]) == float(Q[r["id"]]["answer"]) else 0.0 for r in rows]
    fold = [zlib.crc32(Q[r["id"]]["path"].encode()) % 5 for r in rows]
    new = {}
    for f in range(5):
        w = fit([x for x, k in zip(X, fold) if k != f], [t for t, k in zip(y, fold) if k != f])
        for r, x, k in zip(rows, X, fold):
            if k == f:
                new[r["id"]] = predict(w, x)
    if a.model:
        w = fit(X, y)
        json.dump(dict(features=["bias", "logit(confidence)", "log(1+count)", "count is 0"] + KINDS, weights=[round(v, 4) for v in w],
                       fitted_on=a.answers, n=len(rows)), open(a.model, "w"), indent=1)
    with open(a.out, "w") as out:
        for r in A:
            r = dict(r)
            if r["id"] in new:
                r["confidence_before"] = r.get("confidence")
                r["confidence"] = round(new[r["id"]], 4)
                if a.abstain_below is not None and r["confidence"] < a.abstain_below:
                    r["declined_answer"], r["answer"], r["tier"] = r["answer"], None, "cannot determine"
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(new)} confidences recalculated -> {a.out}")


if __name__ == "__main__":
    main()
