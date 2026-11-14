"""Two engines, one answer: for every question pick the answer more likely to be right, with an honest confidence.

    python eval/combine.py answers/A.jsonl answers/B.jsonl answers/A+B.jsonl

For each engine a small logistic model predicts "is this answer exactly right?" from its own confidence, the question
kind, the size of the count, and whether the other engine gave the same answer (two independent witnesses agreeing is
strong evidence). The answer with the higher predicted chance is kept and that chance is its confidence.

Honest reporting: plans are split in 5 groups and each group is decided by models fitted on the other four.
Standard library only.
"""
import json, os, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from calibrate import features, fit, predict  # noqa: E402


def main():
    fa, fb, out_path = sys.argv[1:4]
    Q = {q["id"]: q for q in (json.loads(l) for l in open(os.path.join(HERE, "test_scans.jsonl")))}
    A = {r["id"]: r for r in (json.loads(l) for l in open(fa) if l.strip())}
    B = {r["id"]: r for r in (json.loads(l) for l in open(fb) if l.strip())}
    ids = [i for i in Q if i in A or i in B]

    def feats(r, other, kind):
        same = other is not None and other.get("answer") is not None and float(other["answer"]) == float(r["answer"])
        diff = abs(float(other["answer"]) - float(r["answer"])) if other is not None and other.get("answer") is not None else 0.0
        return features(kind, r.get("confidence"), r["answer"]) + [1.0 if same else 0.0, min(diff, 5.0) / 5.0]

    fold = {i: zlib.crc32(Q[i]["path"].encode()) % 5 for i in ids}
    prob = {"A": {}, "B": {}}
    for name, mine, other in (("A", A, B), ("B", B, A)):
        rows = [(i, feats(mine[i], other.get(i), Q[i]["kind"]), 1.0 if float(mine[i]["answer"]) == float(Q[i]["answer"]) else 0.0)
                for i in ids if i in mine and mine[i].get("answer") is not None]
        for f in range(5):
            w = fit([x for i, x, _ in rows if fold[i] != f], [t for i, _, t in rows if fold[i] != f])
            for i, x, _ in rows:
                if fold[i] == f:
                    prob[name][i] = predict(w, x)
    picked = {"A": 0, "B": 0}
    with open(out_path, "w") as out:
        for i in ids:
            pa, pb = prob["A"].get(i, -1), prob["B"].get(i, -1)
            name, r, p = ("A", A[i], pa) if pa >= pb else ("B", B[i], pb)
            picked[name] += 1
            other = (B if name == "A" else A).get(i)
            row = dict(id=i, answer=r["answer"], confidence=round(p, 4), tier=r.get("tier", "detected"),
                       method=f"{r.get('method', name)} (chosen of two)", cost_usd=(r.get("cost_usd") or 0) + ((other or {}).get("cost_usd") or 0),
                       other_answer=(other or {}).get("answer"), agree=other is not None and other.get("answer") == r["answer"])
            if r.get("evidence"):
                row["evidence"] = r["evidence"]
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(ids)} questions; kept {picked['A']} answers from {os.path.basename(fa)}, {picked['B']} from {os.path.basename(fb)} -> {out_path}")


if __name__ == "__main__":
    main()
