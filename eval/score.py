"""Shared scoreboard: score any engine's answer file on the shared test questions.

    python eval/score.py ANSWERS.jsonl --name c26l-engine --who aleks [--note "..."] [--no-record]

Reads eval/test_scans.jsonl (the questions) and ANSWERS.jsonl (format in eval/README.md), prints the table,
appends one line per run to scores/history.csv and rewrites scores/README.md (the leaderboard).
Standard library only.
"""
import argparse, collections, csv, datetime, json, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEST = os.path.join(HERE, "test_scans.jsonl")
HISTORY = os.path.join(ROOT, "scores", "history.csv")
BOARD = os.path.join(ROOT, "scores", "README.md")
TIERS = {"read", "measured", "detected", "estimated", "cannot determine"}
FIELDS = ["when", "who", "name", "commit", "questions", "accuracy", "answered", "accuracy_when_answered",
          "confidence_gap", "brier", "cost_per_question_usd", "by_kind", "note"]


def correct(pred, q):
    """The dataset's rule: counts exact; areas, lengths and volumes within 2%."""
    if pred is None:
        return False
    try:
        if q["type"] == "count":
            return float(pred) == float(q["answer"])
        return abs(float(pred) - q["answer"]) <= 0.02 * abs(q["answer"])
    except (TypeError, ValueError):
        return False


def load_answers(path):
    out, problems = {}, []
    for n, line in enumerate(open(path), 1):
        if not line.strip():
            continue
        a = json.loads(line)
        if "id" not in a or "answer" not in a:
            problems.append(f"line {n}: needs 'id' and 'answer'")
            continue
        if a.get("tier") is not None and a["tier"] not in TIERS:
            problems.append(f"line {n}: tier {a['tier']!r} is not one of {sorted(TIERS)}")
        c = a.get("confidence")
        if c is not None and not 0 <= c <= 1:
            problems.append(f"line {n}: confidence {c} is not between 0 and 1")
        out[a["id"]] = a
    return out, problems


def confidence_stats(rows):
    """rows: (confidence, was_correct) for answered questions that carry a confidence.

    confidence_gap is the expected calibration error over 10 equal-width bins: the average distance
    between stated confidence and the share that was right. 0 is perfect. brier is the mean squared error
    of the confidence against right/wrong (lower is better)."""
    if not rows:
        return None, None, []
    bins = collections.defaultdict(list)
    for c, ok in rows:
        bins[min(int(c * 10), 9)].append((c, ok))
    gap = sum(abs(sum(c for c, _ in b) / len(b) - sum(ok for _, ok in b) / len(b)) * len(b) for b in bins.values()) / len(rows)
    brier = sum((c - ok) ** 2 for c, ok in rows) / len(rows)
    table = [(i / 10, (i + 1) / 10, len(b), sum(c for c, _ in b) / len(b), sum(ok for _, ok in b) / len(b))
             for i, b in sorted(bins.items())]
    return gap, brier, table


def score(questions, answers):
    per = collections.defaultdict(lambda: dict(n=0, ok=0, answered=0, ok_answered=0))
    conf_rows, cost, cost_n = [], 0.0, 0
    for q in questions:
        a = answers.get(q["id"])
        pred = a.get("answer") if a else None
        ok = correct(pred, q)
        for key in (q["kind"], "ALL"):
            s = per[key]
            s["n"] += 1
            s["ok"] += ok
            if pred is not None:
                s["answered"] += 1
                s["ok_answered"] += ok
        if a and pred is not None and a.get("confidence") is not None:
            conf_rows.append((a["confidence"], ok))
        if a and a.get("cost_usd") is not None:
            cost += a["cost_usd"]
            cost_n += 1
    return per, conf_rows, (cost / cost_n if cost_n else None)


def pct(x):
    return "" if x is None else f"{100 * x:.1f}%"


def write_board():
    rows = list(csv.DictReader(open(HISTORY)))
    latest = {}
    for r in rows:
        latest[r["name"]] = r
    best = sorted(latest.values(), key=lambda r: -float(r["accuracy"]))
    kinds = sorted({k for r in best for k in json.loads(r["by_kind"])})
    L = ["# Scoreboard", "",
         "Shared test: the 241 preview scans, 4,270 count questions (`eval/test_scans.jsonl`). Counts must be exact.",
         "Made by `eval/score.py`; do not edit by hand. Newest run of each engine, best first. Format and rules: `eval/README.md`.", "",
         "| Engine | Who | Accuracy | Answered | Accuracy when answered | Confidence gap | Cost per question | When | Note |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in best:
        cost = f"${float(r['cost_per_question_usd']):.5f}" if r["cost_per_question_usd"] else ""
        gap = f"{100 * float(r['confidence_gap']):.1f} pts" if r["confidence_gap"] else ""
        L.append(f"| {r['name']} | {r['who']} | **{pct(float(r['accuracy']))}** | {pct(float(r['answered']))} | "
                 f"{pct(float(r['accuracy_when_answered']))} | {gap} | {cost} | {r['when']} | {r['note']} |")
    L += ["", "Accuracy counts a missing or \"cannot determine\" answer as wrong. Confidence gap: the average distance between",
          "the confidence an engine states and how often it is right at that confidence (0 is perfect).", "",
          "## Accuracy by question kind", "", "| Engine | " + " | ".join(kinds) + " |", "|---|" + "---|" * len(kinds)]
    for r in best:
        k = json.loads(r["by_kind"])
        L.append(f"| {r['name']} | " + " | ".join(pct(k.get(x)) for x in kinds) + " |")
    L += ["", f"All runs: `scores/history.csv` ({len(rows)} so far)."]
    open(BOARD, "w").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("answers")
    ap.add_argument("--name", required=True, help="engine name on the leaderboard, e.g. drawqa-c26l or rival-v1")
    ap.add_argument("--who", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--no-record", action="store_true", help="print only; do not touch scores/")
    a = ap.parse_args()

    questions = [json.loads(l) for l in open(TEST)]
    answers, problems = load_answers(a.answers)
    for p in problems[:10]:
        print("FORMAT:", p)
    if problems:
        raise SystemExit(f"{len(problems)} format problems; fix the answer file (eval/README.md)")
    unknown = set(answers) - {q["id"] for q in questions}
    if unknown:
        raise SystemExit(f"{len(unknown)} ids are not in the test set, e.g. {sorted(unknown)[:3]}")

    per, conf_rows, cost = score(questions, answers)
    gap, brier, table = confidence_stats(conf_rows)
    print(f"{'kind':14s} {'correct':>11s} {'accuracy':>9s} {'answered':>9s} {'acc. when answered':>19s}")
    for k in sorted(per, key=lambda k: (k == "ALL", k)):
        s = per[k]
        print(f"{k:14s} {s['ok']:5d}/{s['n']:<5d} {pct(s['ok'] / s['n']):>9s} {pct(s['answered'] / s['n']):>9s} "
              f"{pct(s['ok_answered'] / s['answered']) if s['answered'] else '':>19s}")
    if table:
        print("\nconfidence stated -> share actually right")
        for lo, hi, n, mean_c, right in table:
            print(f"  {lo:.1f}-{hi:.1f}  n={n:<5d} stated {pct(mean_c):>6s}  right {pct(right):>6s}")
        print(f"confidence gap {100 * gap:.1f} points, brier {brier:.4f}")
    else:
        print("\nno confidence values in this answer file")
    if cost is not None:
        print(f"cost per question ${cost:.5f}")

    if a.no_record:
        return
    s = per["ALL"]
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = ""
    row = dict(when=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), who=a.who, name=a.name, commit=commit,
               questions=s["n"], accuracy=round(s["ok"] / s["n"], 4), answered=round(s["answered"] / s["n"], 4),
               accuracy_when_answered=round(s["ok_answered"] / s["answered"], 4) if s["answered"] else 0,
               confidence_gap="" if gap is None else round(gap, 4), brier="" if brier is None else round(brier, 4),
               cost_per_question_usd="" if cost is None else round(cost, 6),
               by_kind=json.dumps({k: round(v["ok"] / v["n"], 4) for k, v in sorted(per.items()) if k != "ALL"}),
               note=a.note)
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    new = not os.path.exists(HISTORY)
    with open(HISTORY, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    write_board()
    print(f"\nrecorded in scores/history.csv; leaderboard: scores/README.md")


if __name__ == "__main__":
    main()
