"""Answers in the dataset's questions.jsonl that count what is NOT visible on the drawing -> data/answer_corrections.jsonl.

    .venv/bin/python tools/make_answer_corrections.py

Swiss Dwellings DXFs often store the same door or window twice, exactly on top of itself (on the 65 preview plans: 273 of
the 276 repeated door openings are one leaf drawn twice; 61 plans have such copies). The dataset's door and window counts
include those copies, so they are higher than what a person counts on the drawing (site1000: 36 in the answer, 29 drawn).
A question is corrected only when its answer equals the count WITH the copies and differs from the count without them.
The original questions.jsonl is never changed; drawqa/eval.py applies these corrections and says how many it used.
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from drawqa import swiss
from drawqa.solve import solve, source_of

ROOT = os.path.join(os.path.dirname(__file__), "..", "junction-dataset-preview")
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "answer_corrections.jsonl")

rows = []
for line in open(os.path.join(ROOT, "questions.jsonl")):
    q = json.loads(line)
    if q.get("type") != "count" or source_of(q["path"]) != "swiss_dwellings":
        continue
    r = solve(os.path.join(ROOT, q["path"]), q["question"])
    intent = r.get("intent") or {}
    if intent.get("intent") != "count_openings" or intent.get("cls") not in ("door", "window"):
        continue
    sheet = swiss.load(os.path.join(ROOT, os.path.splitext(q["path"])[0] + ".dxf"))
    cls = intent["cls"]
    drawn = sum(1 for o in sheet["openings"] if o["cls"] == cls)
    stored = drawn + (sheet.get("duplicates_dropped") or {}).get(cls, 0)
    if q["answer"] == stored and stored != drawn:
        rows.append(dict(path=q["path"], question=q["question"], dataset_answer=q["answer"], answer=drawn,
                         reason=f"the dataset counts {stored - drawn} {cls}(s) that the DXF stores twice, exactly on top of "
                                f"another one; the drawing shows {drawn}"))
with open(OUT, "w") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"{len(rows)} corrections written to {OUT}")
