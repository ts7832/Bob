"""Write Aleks's engine (drawqa) answers to the shared test questions in the scoreboard's answer format.

    python eval/export_drawqa.py answers/drawqa.jsonl

Uses drawqa.solve.solve() exactly as drawqa/eval.py does, so the predictions file named in
data/predictions/active.txt decides which detector is scored. Needs the test images in
junction-dataset-preview/data/cubicasa5k/ (the preview zip, or a link to the full dataset).
"""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from drawqa.solve import solve  # noqa: E402


def main():
    out_path = sys.argv[1]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    data = os.path.join(ROOT, "junction-dataset-preview")
    n = errors = 0
    with open(out_path, "w") as out:
        for line in open(os.path.join(HERE, "test_scans.jsonl")):
            q = json.loads(line)
            t0 = time.time()
            try:
                r = solve(os.path.join(data, q["path"]), q["question"])
                row = dict(id=q["id"], answer=r.get("answer"), confidence=r.get("confidence"),
                           tier=(r.get("honesty") or {}).get("tier"), method=r.get("method"), cost_usd=0.0)
            except Exception as e:  # one bad drawing must not stop the export
                errors += 1
                row = dict(id=q["id"], answer=None, tier="cannot determine", method=f"ERROR {type(e).__name__}: {e}"[:200])
            row["seconds"] = round(time.time() - t0, 3)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} answers written to {out_path} ({errors} errors)")


if __name__ == "__main__":
    main()
