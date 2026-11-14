"""Summary of a cascade test run (library/eval_cascade.py).

    python library/eval_summary.py library/eval_out/cascade_<stamp>.jsonl

Accuracy by source and question kind, patterns only vs with the Jev-style router, and how well confidence predicts
correctness: calibration table, AUROC, and accuracy when only the most confident share is answered.
"""
import collections, json, sys


def auroc(pairs):
    """Probability that a random right answer has higher confidence than a random wrong one."""
    pos = [c for c, ok in pairs if ok]
    neg = [c for c, ok in pairs if not ok]
    if not pos or not neg:
        return None
    allv = sorted([(c, 1) for c in pos] + [(c, 0) for c in neg])
    rank, i, ranks_pos = 1, 0, 0.0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        avg = (rank + rank + (j - i) - 1) / 2
        ranks_pos += avg * sum(1 for k in range(i, j) if allv[k][1])
        rank += j - i
        i = j
    return (ranks_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def pct(a, b):
    return f"{100 * a / b:5.1f}%" if b else "   - "


rows = [json.loads(l) for l in open(sys.argv[1])]
data = [r for r in rows if r["set"] == "dataset"]
rew = [r for r in rows if r["set"] != "dataset"]


def table(rs, key, title):
    print(f"\n{title}")
    print(f"  {'':34s} {'questions':>9s} {'patterns only':>14s} {'with Jev router':>16s} {'read by Jev':>12s}")
    g = collections.defaultdict(list)
    for r in rs:
        g[key(r)].append(r)
    for k in sorted(g, key=lambda k: -len(g[k])) + ["ALL"]:
        rr = rs if k == "ALL" else g[k]
        pat = sum(r["ok"] for r in rr if r["read_by"] == "pattern router")
        cas = sum(r["ok"] for r in rr)
        jev = sum(1 for r in rr if r["read_by"] == "jev router")
        print(f"  {str(k)[:34]:34s} {len(rr):9d} {pct(pat, len(rr)):>14s} {pct(cas, len(rr)):>16s} {jev:12d}")


table(data, lambda r: r["source"], "DATASET QUESTIONS, by source (every question with a number for an answer; scans: the 241 test plans)")
table(data, lambda r: r["kind"], "DATASET QUESTIONS, by kind of question")
for st in sorted({r["set"] for r in rew}):
    table([r for r in rew if r["set"] == st], lambda r: r["kind"], f"HELD-OUT REWORDINGS ({st}), by kind")

print("\nCONFIDENCE vs CORRECTNESS (engine's own confidence on answers it gave)")
for name, rs in [("dataset", data)] + [(st, [r for r in rew if r["set"] == st]) for st in sorted({r["set"] for r in rew})]:
    pairs = [(float(r["conf"]), bool(r["ok"])) for r in rs if r["answer"] is not None and isinstance(r["conf"], (int, float))]
    if not pairs:
        continue
    bins = collections.defaultdict(list)
    for c, ok in pairs:
        bins[min(int(c * 10), 9)].append((c, ok))
    gap = sum(abs(sum(c for c, _ in b) / len(b) - sum(o for _, o in b) / len(b)) * len(b) for b in bins.values()) / len(pairs)
    print(f"  {name}: {len(pairs)} answers with a confidence | AUROC {auroc(pairs):.3f} | confidence gap {100 * gap:.1f} points")
    for k in sorted(bins):
        b = bins[k]
        print(f"     stated {k / 10:.1f}-{(k + 1) / 10:.1f}: n={len(b):6d}  mean stated {100 * sum(c for c, _ in b) / len(b):5.1f}%  actually right {100 * sum(o for _, o in b) / len(b):5.1f}%")
    srt = sorted(pairs, key=lambda x: -x[0])
    print("     answer only the most confident:", " | ".join(f"top {int(f * 100)}% -> {100 * sum(o for _, o in srt[:max(1, int(len(srt) * f))]) / max(1, int(len(srt) * f)):.1f}% right"
                                                         for f in (0.5, 0.8, 0.9, 1.0)))

print("\nJEV ROUTER: its probability vs whether the final answer was right (questions it read)")
for name, rs in [("dataset", data)] + [(st, [r for r in rew if r["set"] == st]) for st in sorted({r["set"] for r in rew})]:
    pairs = [(float(r["jev_p"]), bool(r["ok"])) for r in rs if r["read_by"] == "jev router" and r["jev_p"] is not None]
    if len(pairs) < 5:
        continue
    print(f"  {name}: {len(pairs)} questions read by the Jev router | answer right {pct(sum(o for _, o in pairs), len(pairs))} | AUROC {auroc(pairs) or 0:.3f}")
    for lo, hi in ((0.5, 0.7), (0.7, 0.9), (0.9, 1.01)):
        b = [o for c, o in pairs if lo <= c < hi]
        print(f"     router probability {lo:.1f}-{min(hi, 1):.1f}: n={len(b):5d}  answer right {pct(sum(b), len(b))}")

print("\nNOT ANSWERED")
for name, rs in [("dataset", data)] + [(st, [r for r in rew if r["set"] == st]) for st in sorted({r["set"] for r in rew})]:
    c = collections.Counter(r["read_by"] for r in rs)
    errs = sum(1 for r in rs if r["err"])
    print(f"  {name}: read by {dict(c)}; engine errors {errs}")
