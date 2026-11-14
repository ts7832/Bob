"""Scoreboard: python -m drawqa.eval [--source swiss_dwellings] [--limit N]

Scoring rules from the dataset README: counts exact; areas/lengths/volumes within 2%; text skipped here.
"""
import argparse, json, os, collections, time
from .solve import solve, source_of
from . import scan

ROOT = os.path.join(os.path.dirname(__file__), "..", "junction-dataset-preview")


def correct(pred, q):
    if pred is None:
        return False
    if q["type"] == "count":
        return pred == q["answer"]
    return abs(pred - q["answer"]) <= 0.02 * abs(q["answer"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--show-wrong", type=int, default=3)
    a = ap.parse_args()
    Q = [json.loads(l) for l in open(os.path.join(ROOT, "questions.jsonl"))]
    Q = [q for q in Q if q["type"] != "text" and (not a.source or source_of(q["path"]) == a.source)]
    Q = [q for q in Q if source_of(q["path"]) != "cubicasa5k" or os.path.basename(q["path"]) in scan.predictions()]
    if a.limit:
        Q = Q[: a.limit]
    S = collections.defaultdict(lambda: [0, 0])
    wrong = collections.defaultdict(list)
    t0 = time.time()
    for q in Q:
        try:
            r = solve(os.path.join(ROOT, q["path"]), q["question"])
            pred = r.get("answer")
            key = (source_of(q["path"]), r["intent"]["intent"] + (f":{r['intent'].get('cls')}" if r["intent"].get("cls") else ""))
        except Exception as e:  # never let one drawing kill the scoreboard
            pred, key = None, (source_of(q["path"]), f"ERROR {type(e).__name__}")
        ok = correct(pred, q)
        S[key][0] += ok
        S[key][1] += 1
        if not ok and len(wrong[key]) < a.show_wrong:
            wrong[key].append((q["question"][:60], q["answer"], pred))
    by_src = collections.defaultdict(lambda: [0, 0])
    print(f"{'source':16s} {'intent':32s} {'correct':>9s}")
    for (src, k), (ok, n) in sorted(S.items()):
        by_src[src][0] += ok
        by_src[src][1] += n
        print(f"{src:16s} {k:32s} {ok:4d}/{n:<4d} {100 * ok / n:5.1f}%")
        for w in wrong[(src, k)]:
            print(f"{'':50s} ✗ {w}")
    print("\nPER SOURCE (all languages)")
    for src, (ok, n) in by_src.items():
        print(f"  {src:16s} {ok}/{n} = {100 * ok / n:.1f}%")
    print(f"({time.time() - t0:.0f}s; cubicasa only on plans with detector predictions)")


if __name__ == "__main__":
    main()
