"""Division of labour, measured on runs we already have: Claude's ROOM layer (+ optionally its floors and scale) merged
with the detector's doors, windows and fixtures, untouched. Scored by our engine like everything else.

    python merge_test.py        (from the repo root)
"""
import json, os, re, subprocess, sys

P = os.path.dirname(os.path.abspath(__file__))
IMG = "junction-dataset-preview/data/cubicasa5k/{}.png"


def engine(plan, model, keys):
    tmp = os.path.join(P, "merge_tmp.json")
    json.dump(model, open(tmp, "w"))
    out = subprocess.run([sys.executable, f"{P}/engine_on_model.py", IMG.format(plan), tmp, f"{keys}/KEY.json", f"{keys}/ENGINE.json"],
                         capture_output=True, text=True, env=dict(os.environ, DRAWQA_OUTLINES="0")).stdout
    rows = [re.match(r"(OK |X  ) key=(\S+)\s+frontier-sheet=(\S+)\s+detector=(\S+)\s+(.*)", l) for l in out.splitlines()]
    return [r for r in rows if r]


VARIANTS = {
    "full review": lambda s, f: f,
    "Claude rooms + detector symbols": lambda s, f: dict(s, rooms=f.get("rooms", [])),
    "Claude rooms, floors, scale + detector symbols": lambda s, f: dict(s, rooms=f.get("rooms", []), floors=f.get("floors", s.get("floors")),
                                                                        m_per_px=f.get("m_per_px"), scale_source=f.get("scale_source", "")),
}
for fix, keys, label in (("fix", "fixkeys", "first set (reviewer could add anything)"), ("fix2", "fixkeys2", "second set (constrained reviewer)")):
    tot = {"detector": 0, **{v: 0 for v in VARIANTS}}
    n = 0
    by_q = {}
    for plan in sorted(os.listdir(f"{P}/{fix}")):
        d = f"{P}/{fix}/{plan}"
        if not os.path.exists(f"{d}/model_final.json"):
            continue
        s, f = json.load(open(f"{d}/start_model.json")), json.load(open(f"{d}/model_final.json"))
        for v, make in VARIANTS.items():
            rows = engine(plan, make(s, f), f"{P}/{keys}/{plan}")
            tot[v] += sum(r.group(1) == "OK " for r in rows)
            if v == "Claude rooms + detector symbols":
                n += len(rows)
                tot["detector"] += sum(r.group(2) == r.group(4) for r in rows)
                for r in rows:
                    kind = re.sub(r"\s*\(.*", "", r.group(5))[9:40]
                    g = by_q.setdefault(kind, [0, 0, 0])
                    g[0] += 1; g[1] += r.group(2) == r.group(4); g[2] += r.group(1) == "OK "
    print(f"\n== {label}: {n} questions")
    for k, v in tot.items():
        print(f"   {k:48s} {v:3d}/{n}")
    print("   by question (detector -> Claude rooms + detector symbols):")
    for k, (m, a, b) in sorted(by_q.items()):
        if a != b:
            print(f"      {k:32s} {a}/{m} -> {b}/{m}")
