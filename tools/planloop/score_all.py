"""Score every finished v2_* run: our engine on the frontier model's sheet, first round and final round.

    python score_all.py        (from the repo root)
"""
import glob, json, os, re, subprocess, sys

P = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
IMG = "junction-dataset-preview/data/cubicasa5k/high_quality_architectural-{}.png"


def engine(plan, model):
    out = subprocess.run([PY, f"{P}/engine_on_model.py", IMG.format(plan), model, f"{P}/high_quality_architectural-{plan}.KEY.json",
                          f"{P}/high_quality_architectural-{plan}.ENGINE.json"], capture_output=True, text=True, env=dict(os.environ, DRAWQA_OUTLINES="0"))
    rows = [l for l in out.stdout.splitlines() if l.startswith(("OK ", "X  "))]
    tail = [l for l in out.stdout.splitlines() if l.startswith("our engine")]
    return rows, (tail[0] if tail else out.stderr[-300:])


for d in sorted(glob.glob(f"{P}/v2_*")):
    name = os.path.basename(d)
    plan = name.rsplit("_", 1)[-1]
    versions = sorted(glob.glob(f"{d}/model_v*.json"), key=lambda f: int(re.search(r"model_v(\d+)", f).group(1)))
    final = f"{d}/model_final.json" if os.path.exists(f"{d}/model_final.json") else (versions[-1] if versions else None)
    if not final:
        print(f"{name}: no model yet")
        continue
    first_rows, first = engine(plan, versions[0]) if versions else ([], "")
    rows, last = engine(plan, final)
    print(f"\n== {name}: {len(versions)} versions")
    print(f"   round 1: {first}")
    print(f"   final:   {last}")
    for r in rows:
        if r.startswith("X"):
            print("     ", r)
