"""Gold-set scoreboard: python tools/gold_eval.py [--drafts] [--llm] [--only g001,g002] [--origin judge-style]

Runs drawqa.solve.solve() on every scored line of gold/gold.jsonl and grades two things:

  value    counts exact; areas, lengths and volumes within 2% after unit conversion (dataset README rule).
           An expected answer of null means "cannot determine" is the right reply.
  honesty  how the answer relates to the tier the system claimed (drawqa/solve.py honesty()):
             correct            right value (or a correct refusal)
             wrong_confident    wrong value presented as read / measured: the failure judges punish most
             wrong_flagged      wrong value, but tier detected / estimated warned the reader
             false_refusal      answered "cannot determine" though the sheet has the answer
             missed_refusal     gave a number where the right reply is "cannot determine"
             error              solve() raised; the exception is in the report
           plus whether the claimed tier equals the expected tier when one is given.

Only `verified` lines are scored unless --drafts, which also scores `proposed` values and marks them as drafts.
Writes gold/reports/<timestamp>.json and .md.
"""
import argparse, collections, datetime, json, os, re, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
ROOT = os.path.join(REPO, "junction-dataset-preview")
GOLD = os.path.join(REPO, "gold", "gold.jsonl")
OUT = os.path.join(REPO, "gold", "reports")

TOL = 0.02
CONFIDENT = ("read", "measured")
# unit -> (dimension, factor to SI)
UNITS = {
    "count": ("count", 1.0),
    "m2": ("area", 1.0), "ft2": ("area", 0.09290304), "sf": ("area", 0.09290304),
    "m": ("length", 1.0), "mm": ("length", 0.001), "cm": ("length", 0.01), "ft": ("length", 0.3048), "in": ("length", 0.0254),
    "m3": ("volume", 1.0), "ft3": ("volume", 0.028316846592),
}
ALIASES = {"m²": "m2", "sq m": "m2", "sqm": "m2", "ft²": "ft2", "sq ft": "ft2", "sqft": "ft2", "SF": "ft2",
           "m³": "m3", "ft³": "ft3", "cu ft": "ft3", "\"": "in", "'": "ft", None: None}


def norm_unit(u):
    if u is None:
        return None
    u = ALIASES.get(u, ALIASES.get(u.strip(), u.strip()))
    return u.lower() if u and u.lower() in UNITS else u


def convert(value, from_unit, to_unit):
    """value in from_unit expressed in to_unit; raises ValueError when the dimensions differ or a unit is unknown."""
    fu, tu = norm_unit(from_unit), norm_unit(to_unit)
    if fu == tu:
        return value
    if fu not in UNITS or tu not in UNITS:
        raise ValueError(f"unknown unit {from_unit!r} or {to_unit!r}")
    (fd, ff), (td, tf) = UNITS[fu], UNITS[tu]
    if fd != td:
        raise ValueError(f"cannot compare {from_unit} with {to_unit}")
    return value * ff / tf


NUM = re.compile(r"^\s*(-?[\d,]*\.?\d+)\s*([A-Za-z²³'\"]+[ A-Za-z²³]*)?\s*$")


def parse_answer(pred, pred_unit):
    """A number, or a string such as '5,183 SF' -> (5183.0, 'ft2'). (None, None, note) when it is not one number."""
    if pred is None or isinstance(pred, (int, float)) and not isinstance(pred, bool):
        return pred, pred_unit, ""
    m = NUM.match(str(pred))
    if not m:
        return None, pred_unit, f"answer is not a single number: {pred!r}"
    return float(m.group(1).replace(",", "")), pred_unit or (m.group(2) or "").strip() or None, f"parsed {pred!r}"


def value_ok(pred, pred_unit, expected, exp_unit, qtype):
    """(ok, note). expected None = refusal is correct."""
    raw = pred
    pred, pred_unit, note = parse_answer(pred, pred_unit)
    if expected is None:
        return raw is None, note
    if pred is None:
        return False, note
    if qtype != "count":
        if pred_unit is None:
            note = (note + "; " if note else "") + f"system gave no unit; compared as {exp_unit}"
        else:
            try:
                pred = convert(pred, pred_unit, exp_unit)
            except ValueError as e:
                return False, str(e)
            if norm_unit(pred_unit) != norm_unit(exp_unit):
                note = (note + "; " if note else "") + f"converted {pred_unit} -> {exp_unit}"
        return abs(pred - expected) <= TOL * abs(expected), note
    return pred == expected, note


def grade(q, r, draft):
    expected = q["proposed_answer"] if draft else q["answer"]
    tier = (r.get("honesty") or {}).get("tier")
    pred = r.get("answer")
    ok, note = value_ok(pred, r.get("unit"), expected, q.get("unit"), q.get("type"))
    if ok:
        outcome = "correct"
    elif expected is None:
        outcome = "missed_refusal"
    elif pred is None:
        outcome = "false_refusal"
    else:
        outcome = "wrong_confident" if tier in CONFIDENT else "wrong_flagged"
    exp_tier = q.get("expected_tier")
    return dict(outcome=outcome, value_ok=ok, note=note, expected=expected, pred=pred, pred_unit=r.get("unit"),
                tier=tier, expected_tier=exp_tier, tier_ok=(tier == exp_tier) if exp_tier else None)


def load_gold(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{n}: not valid JSON ({e})")
    ids = collections.Counter(r["id"] for r in rows)
    dup = [i for i, c in ids.items() if c > 1]
    if dup:
        raise SystemExit(f"duplicate ids in {path}: {dup}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drafts", action="store_true", help="also score proposed (unchecked) values")
    ap.add_argument("--llm", action="store_true", help="let solve() use the language model for free wording")
    ap.add_argument("--only", help="comma-separated ids")
    ap.add_argument("--origin", help="dataset | judge-style | honesty")
    ap.add_argument("--gold", default=GOLD)
    a = ap.parse_args()

    from drawqa.solve import solve  # heavy import after argument errors

    gold = load_gold(a.gold)
    picked = []
    for q in gold:
        if a.only and q["id"] not in a.only.split(","):
            continue
        if a.origin and q.get("origin") != a.origin:
            continue
        if q["status"] == "verified":
            picked.append((q, False))
        elif a.drafts and q["status"] == "proposed":
            picked.append((q, True))
    chosen = {q["id"] for q, _ in picked}
    skipped = collections.Counter(q["status"] for q in gold if q["id"] not in chosen)
    if not picked:
        raise SystemExit(f"nothing to score in {a.gold} (statuses: {dict(collections.Counter(q['status'] for q in gold))}); "
                         "verify some lines or pass --drafts")

    results = []
    t_all = time.time()
    for q, draft in picked:
        path = os.path.join(ROOT, q["path"])
        if not os.path.exists(path):
            raise SystemExit(f"{q['id']}: file not found: {path}")
        t0 = time.time()
        try:
            r = solve(path, q["question"], llm=a.llm)
        except Exception as e:  # the system failed on this question: an outcome, reported with its traceback
            r = e
        if not isinstance(r, Exception):  # grading errors are bugs in this script and stop the run
            g = grade(q, r, draft)
            g["explanation"] = r.get("explanation")
            g["method"] = r.get("method")
            ev = r.get("evidence") or []
            g["evidence_items"] = len(ev)
            # boxes on the page image, for tools/gold_gallery.py (GUID-only evidence from BIM models has no position)
            g["evidence"] = [dict(id=e.get("id"), label=e.get("label"), bbox=e["bbox"]) for e in ev if e.get("bbox")][:400]
            g["source_type"] = r.get("source_type")
            g["basis"] = (r.get("honesty") or {}).get("basis")
        else:
            e = r
            g = dict(outcome="error", value_ok=False, expected=q["proposed_answer"] if draft else q["answer"], pred=None,
                     tier=None, expected_tier=q.get("expected_tier"), tier_ok=None,
                     error=f"{type(e).__name__}: {e}", traceback="".join(traceback.format_exception(e, limit=4)))
        g.update(id=q["id"], path=q["path"], question=q["question"], origin=q.get("origin"), type=q.get("type"),
                 unit=q.get("unit"), draft=draft, seconds=round(time.time() - t0, 2))
        results.append(g)
        mark = "✓" if g["value_ok"] else "✗"
        print(f"{mark} {g['id']:6s} {g['outcome']:16s} exp={g['expected']!s:>10} got={g['pred']!s:>10} "
              f"tier={g['tier']!s:17s}{' DRAFT' if draft else ''}  {q['question'][:60]}")
        if g["outcome"] == "error":
            print("   " + g["error"])

    summary = summarize(results)
    summary.update(seconds=round(time.time() - t_all, 1), llm=a.llm, drafts_included=a.drafts,
                   not_scored=dict(skipped))
    write_reports(results, summary)


def summarize(results):
    n = len(results)
    out = collections.Counter(r["outcome"] for r in results)
    tiers = [r for r in results if r["tier_ok"] is not None]
    by_origin = collections.defaultdict(lambda: [0, 0])
    for r in results:
        by_origin[r["origin"]][0] += r["value_ok"]
        by_origin[r["origin"]][1] += 1
    return dict(n=n, correct=out["correct"], accuracy=round(out["correct"] / n, 3), outcomes=dict(out),
                tier_match=f"{sum(r['tier_ok'] for r in tiers)}/{len(tiers)}" if tiers else "n/a",
                by_origin={k: f"{v[0]}/{v[1]}" for k, v in by_origin.items()},
                drafts=sum(r["draft"] for r in results))


def write_reports(results, s):
    os.makedirs(OUT, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    with open(os.path.join(OUT, f"{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(dict(summary=s, results=results), f, ensure_ascii=False, indent=1)
    L = [f"# Gold scoreboard {stamp}", "",
         f"**{s['correct']}/{s['n']} correct ({100 * s['accuracy']:.1f}%)**"
         + (f", of which {s['drafts']} scored against unchecked drafts" if s["drafts"] else "")
         + f". Tier as expected: {s['tier_match']}. {s['seconds']} s, llm={'on' if s['llm'] else 'off'}.", "",
         "| outcome | n |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(s["outcomes"].items())]
    L += ["", "By origin: " + ", ".join(f"{k} {v}" for k, v in s["by_origin"].items()), "",
          "## Not correct", "", "| id | outcome | expected | got | tier | question | why |", "|---|---|---|---|---|---|---|"]
    for r in results:
        if r["outcome"] != "correct":
            why = (r.get("error") or r.get("explanation") or "").replace("|", "/").replace("\n", " ")[:160]
            L.append(f"| {r['id']}{' (draft)' if r['draft'] else ''} | {r['outcome']} | {r['expected']} {r.get('unit') or ''} | "
                     f"{r['pred']} {r.get('pred_unit') or ''} | {r['tier']} | {r['question'][:70]} | {why} |")
    with open(os.path.join(OUT, f"{stamp}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\n{s['correct']}/{s['n']} correct; outcomes {s['outcomes']}; tier as expected {s['tier_match']}")
    print(f"report: gold/reports/{stamp}.md")


if __name__ == "__main__":
    main()
