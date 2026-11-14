"""Both routers on every question: where they disagree, which reading to trust, and what disagreement says about
correctness. Input: a run of library/eval_cascade.py made with the both-routers cascade.

    python library/eval_policies.py library/eval_out/cascade_<stamp>.jsonl

Policies (all on the same questions):
  patterns      the pattern router only (no answer when it cannot read the question)
  cascade       patterns first, the Jev-style router when patterns read nothing
  override      as cascade, but when the routers disagree and the Jev-style router is at least 90% sure, its reading wins
  best-of-two   upper bound: right if either reading's answer is right (not a real policy)
"""
import collections, json, sys

NAMES = {"count:window": "windows", "count:door": "doors", "count:wall": "walls", "count:stair": "stairs", "count:column": "columns",
         "count:beam": "beams", "count:all_rooms": "rooms (all)", "count:rooms_of_a_type": "rooms of a type", "count:apartments": "apartments",
         "count:floors": "floors", "count:fixtures:wc": "toilets", "count:fixtures:sink": "sinks", "count:fixtures:bathtub": "bathtubs",
         "count:fixtures:shower": "showers", "count:fixtures:shower_or_bathtub": "showers + bathtubs", "count:fixtures:any": "sanitary (any)",
         "area:one_room": "area of one room", "area:whole_floor": "area of a floor", "area:apartment": "area of an apartment",
         "area:rooms_of_a_type": "area of rooms of a type", "area:wall": "wall area", "length:wall": "wall length", "volume:wall": "wall volume",
         "volume:slab": "slab volume", "unlabelled": "(no label)"}
SOURCES = {"swiss_dwellings": "Swiss plans", "cubicasa5k": "Finnish scans", "ifc_models": "3D building models", "opening_design": "US drawing sets"}


def ok_patterns(r):
    return r["ok"] and r["read_by"] == "pattern router"


def ok_cascade(r):
    return r["ok"]


def ok_override(r):
    if r.get("agree") is False and (r.get("jev_p") or 0) >= 0.9 and r.get("alt_answer") is not None:
        return bool(r.get("alt_ok"))
    return r["ok"]


def ok_best(r):
    return r["ok"] or bool(r.get("alt_ok"))


POL = [("patterns", ok_patterns), ("cascade", ok_cascade), ("override", ok_override), ("best-of-two", ok_best)]


def pct(a, b):
    return f"{100 * a / b:5.1f}%" if b else "    -"


def table(rs, key, names, title):
    print(f"\n{title}")
    print(f"  {'':26s} {'n':>6s}" + "".join(f" {p:>11s}" for p, _ in POL) + f" {'disagree':>9s}")
    g = collections.defaultdict(list)
    for r in rs:
        g[key(r)].append(r)
    order = sorted(g, key=lambda k: sum(ok_cascade(r) for r in g[k]) / len(g[k]))     # weakest first
    for k in order + ["ALL"]:
        rr = rs if k == "ALL" else g[k]
        dis = sum(1 for r in rr if r.get("agree") is False)
        print(f"  {names.get(k, k)[:26]:26s} {len(rr):6d}" + "".join(f" {pct(sum(f(r) for r in rr), len(rr)):>11s}" for _, f in POL) + f" {dis:9d}")


def auroc(pairs):
    pos = sorted(c for c, o in pairs if o)
    neg = sorted(c for c, o in pairs if not o)
    if not pos or not neg:
        return float("nan")
    import bisect
    s = 0.0
    for c in pos:
        lo, hi = bisect.bisect_left(neg, c), bisect.bisect_right(neg, c)
        s += lo + 0.5 * (hi - lo)
    return s / (len(pos) * len(neg))


rows = [json.loads(l) for l in open(sys.argv[1])]
sets = ["dataset"] + sorted({r["set"] for r in rows if r["set"] != "dataset"})
for st in sets:
    rs = [r for r in rows if r["set"] == st]
    table(rs, lambda r: r["source"], SOURCES, f"=== {st.upper()} by source, weakest first ===")
    table(rs, lambda r: r["kind"], NAMES, f"=== {st.upper()} by kind of question, weakest first ===")

print("\n=== DISAGREEMENT AS A WARNING SIGN (answers given by the pattern router's reading) ===")
for st in sets:
    rs = [r for r in rows if r["set"] == st and r["read_by"] == "pattern router"]
    ag = [r for r in rs if r.get("agree")]
    dg = [r for r in rs if r.get("agree") is False]
    print(f"  {st:16s} routers agree on {pct(len(ag), len(rs))} of {len(rs)}: right {pct(sum(r['ok'] for r in ag), len(ag))} | "
          f"disagree on {len(dg)}: pattern reading right {pct(sum(r['ok'] for r in dg), len(dg))}, router reading right "
          f"{pct(sum(bool(r.get('alt_ok')) for r in dg), len(dg))}")

print("\n=== CONFIDENCE: engine confidence alone vs lowered when the routers disagree ===")
for st in sets:
    rs = [r for r in rows if r["set"] == st and r["answer"] is not None and isinstance(r["conf"], (int, float))]
    for name, f in (("engine only", lambda r: r["conf"]),
                    ("x router prob, /2 on disagreement", lambda r: r["conf"] * (r["jev_p"] or 1) * (0.5 if r.get("agree") is False else 1))):
        pairs = [(f(r), bool(r["ok"])) for r in rs]
        srt = sorted(pairs, key=lambda x: -x[0])
        tops = " | ".join(f"top {int(q * 100)}%: {pct(sum(o for _, o in srt[:int(len(srt) * q)]), int(len(srt) * q))}" for q in (0.5, 0.8, 0.9))
        print(f"  {st:16s} {name:34s} AUROC {auroc(pairs):.3f} | {tops} | all {pct(sum(o for _, o in pairs), len(pairs))}")
