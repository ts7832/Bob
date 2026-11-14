"""Score the 'detector first, frontier model fixes' runs: our engine on the detector's sheet vs on the corrected sheet.

    python score_fix.py        (from the repo root)
"""
import glob, json, os, re, subprocess, sys

P = os.path.dirname(os.path.abspath(__file__))
IMG = "junction-dataset-preview/data/cubicasa5k/{}.png"
tot = dict(n=0, det=0, first=0, final=0)
changed = []
FIX, KEYS = (sys.argv[1:3] + ["fix", "fixkeys"])[:2] if len(sys.argv) > 2 else ("fix", "fixkeys")
for d in sorted(glob.glob(f"{P}/{FIX}/*")):
    plan = os.path.basename(d)
    vers = sorted(glob.glob(f"{d}/model_v*.json"), key=lambda f: int(re.search(r"model_v(\d+)", f).group(1)))
    final = f"{d}/model_final.json" if os.path.exists(f"{d}/model_final.json") else None
    if not final:
        print(f"{plan:36s} not finished ({len(vers)} versions so far)")
        continue

    def run(model):
        out = subprocess.run([sys.executable, f"{P}/engine_on_model.py", IMG.format(plan), model, f"{P}/{KEYS}/{plan}/KEY.json",
                              f"{P}/{KEYS}/{plan}/ENGINE.json"], capture_output=True, text=True, env=dict(os.environ, DRAWQA_OUTLINES="0"))
        rows = [l for l in out.stdout.splitlines() if l.startswith(("OK ", "X  "))]
        return rows

    first = run(vers[0]) if vers else run(final)
    last = run(final)
    n = len(last)
    det = sum(1 for r in last if re.search(r"key=(\S+)\s+frontier-sheet=\S+\s+detector=(\S+)", r) and
              re.search(r"key=(\S+)", r).group(1) == re.search(r"detector=(\S+)", r).group(1))
    f1 = sum(r.startswith("OK") for r in first)
    fN = sum(r.startswith("OK") for r in last)
    tot["n"] += n; tot["det"] += det; tot["first"] += f1; tot["final"] += fN
    print(f"{plan:36s} questions {n:2d}  detector {det:2d}  after round 1 {f1:2d}  final {fN:2d}  ({len(vers)} versions)")
    for r in last:
        m = re.search(r"key=(\S+)\s+frontier-sheet=(\S+)\s+detector=(\S+)\s+(.*)", r)
        if m and (m.group(2) == m.group(1)) != (m.group(3) == m.group(1)):
            changed.append(f"   {'GAINED' if m.group(2) == m.group(1) else 'LOST  '} {plan[-14:]:14s} key={m.group(1):4s} detector={m.group(3):4s} corrected={m.group(2):4s} {m.group(4)[:60]}")
print(f"\nTOTAL over finished plans: {tot['n']} questions | detector {tot['det']} | corrected after round 1 {tot['first']} | corrected final {tot['final']}")
print("\n".join(changed))
