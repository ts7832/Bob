"""Claude's rooms check the detector's fixtures by fixed rules (Claude never edits a symbol):
  - a fixture whose centre is in no room drops out (it is outside the plan, or in a notes area);
  - a fixture inside a room of a type it cannot belong to drops out (sauna bench in a bedroom, WC in a kitchen).
Rooms typed 'room'/'other_room' (unknown kind) or 'entry'/'utility' allow everything. Doors and windows are untouched.

    python roomfilter_test.py        (from the repo root)
"""
import json, os, re, subprocess, sys

P = os.path.dirname(os.path.abspath(__file__))
IMG = "junction-dataset-preview/data/cubicasa5k/{}.png"
OPEN = {"room", "other_room", "entry", "utility", None}
ALLOWED = {"wc": {"wc_room", "bathroom", "sauna_room"}, "shower": {"bathroom", "wc_room", "sauna_room"},
           "bathtub": {"bathroom", "wc_room", "sauna_room"}, "sauna_stove": {"sauna_room", "bathroom"},
           "sauna_bench": {"sauna_room", "bathroom"}, "sink": {"kitchen", "dining", "bathroom", "wc_room", "sauna_room", "living_room"}}


def inside(x, y, poly):
    c = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def filt(start, rooms):
    keep, dropped = [], []
    for f in start.get("fixtures", []):
        b = f["bbox"]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        hits = [r for r in rooms if len(r.get("polygon", [])) >= 3 and inside(cx, cy, r["polygon"])]
        if rooms and not hits:
            dropped.append((f["type"], "in no room"))
            continue
        types = {r.get("type") for r in hits}
        if hits and not (types & OPEN) and not (types & ALLOWED.get(f["type"], types)):
            dropped.append((f["type"], f"in {'/'.join(sorted(t for t in types if t))}"))
            continue
        keep.append(f)
    return dict(start, rooms=rooms, fixtures=keep), dropped


tot = dict(n=0, det=0, rooms=0, filt=0)
gl = []
for fix, keys in (("fix", "fixkeys"), ("fix2", "fixkeys2")):
    for plan in sorted(os.listdir(f"{P}/{fix}")):
        s, f = json.load(open(f"{P}/{fix}/{plan}/start_model.json")), json.load(open(f"{P}/{fix}/{plan}/model_final.json"))
        res = {}
        for name, m in (("rooms", dict(s, rooms=f.get("rooms", []))), ("filt", filt(s, f.get("rooms", []))[0])):
            json.dump(m, open(f"{P}/rf_tmp.json", "w"))
            out = subprocess.run([sys.executable, f"{P}/engine_on_model.py", IMG.format(plan), f"{P}/rf_tmp.json", f"{P}/{keys}/{plan}/KEY.json",
                                  f"{P}/{keys}/{plan}/ENGINE.json"], capture_output=True, text=True, env=dict(os.environ, DRAWQA_OUTLINES="0")).stdout
            res[name] = list(re.finditer(r"(OK |X  ) key=(\S+)\s+frontier-sheet=(\S+)\s+detector=(\S+)\s+(.*)", out))
        tot["n"] += len(res["filt"])
        tot["det"] += sum(r.group(2) == r.group(4) for r in res["filt"])
        tot["rooms"] += sum(r.group(1) == "OK " for r in res["rooms"])
        tot["filt"] += sum(r.group(1) == "OK " for r in res["filt"])
        dropped = filt(s, f.get("rooms", []))[1]
        for a, b in zip(res["rooms"], res["filt"]):
            if (a.group(1) == "OK ") != (b.group(1) == "OK "):
                gl.append(f"   {'GAINED' if b.group(1) == 'OK ' else 'LOST  '} {plan[-12:]:12s} key={b.group(2)} rooms-only={a.group(3)} filtered={b.group(3)}  {b.group(5)[:55]}")
        if dropped:
            gl.append(f"   dropped on {plan[-12:]}: {dropped}")
print(f"{tot['n']} questions | detector {tot['det']} | Claude rooms + detector symbols {tot['rooms']} | + room-checked fixtures {tot['filt']}")
print("\n".join(gl))
