"""Score the PDF-only route on Swiss plans that are in neither the preview nor the detector's training sites.

    .venv/bin/python tools/eval_heldout.py        (needs data/heldout/: 24 PDFs + questions.jsonl from the Modal volume)
The linework-snapping rule was written while looking at the 65 preview plans; this is the check on plans it never saw.
"""
import collections, json, os, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.environ["DRAWQA_SWISS"] = "pdf"
from drawqa.solve import solve  # noqa: E402
from drawqa.eval import correct  # noqa: E402

H = os.path.join(ROOT, "data", "heldout")
Q = [json.loads(l) for l in open(os.path.join(H, "questions.jsonl"))]
S, t0 = collections.defaultdict(lambda: [0, 0]), time.time()
for q in Q:
    if q["type"] == "text":
        continue
    try:
        r = solve(os.path.join(H, q["path"][len("data/"):]), q["question"])
        key = r["intent"]["intent"] + (f":{r['intent'].get('cls')}" if r["intent"].get("cls") else "")
        ok = correct(r.get("answer"), q)
    except Exception as e:
        key, ok = f"ERROR {type(e).__name__}", False
    S[key][0] += ok
    S[key][1] += 1
for k, (ok, n) in sorted(S.items()):
    print(f"{k:32s} {ok:4d}/{n:<4d} {100 * ok / n:5.1f}%")
a, b = sum(v[0] for v in S.values()), sum(v[1] for v in S.values())
print(f"TOTAL {a}/{b} = {100 * a / b:.1f}%  ({len({q['path'] for q in Q})} plans, {time.time() - t0:.0f}s)")
