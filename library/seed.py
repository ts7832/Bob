"""Put drawings into the library, with the engine's answers to the dataset's questions about them.

    python library/seed.py --project "Esplanades" --answers 12 junction-dataset-preview/data/ifc_models/bsc-esplanades_*plan*.pdf

Uploads each file (the back end renders it, fingerprints it and stores it), then runs the engine (drawqa.solve) on up to
--answers dataset questions per file and stores every answer with the time it took, so the library can answer them
again for nothing. Run from the repo root with the project venv. Password: the BOB_PASSWORD environment variable.
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
URL = os.environ.get("BOB_LIBRARY", "https://oliver-universityapps--bob-library-web.us-east.modal.run")
PW = os.environ.get("BOB_PASSWORD", "123456789")


def call(method, path, body=None, ctype="application/octet-stream", **params):
    url = URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, data=body, method=method, headers={"x-bob-password": PW, "content-type": ctype})
    return json.load(urllib.request.urlopen(req, timeout=300))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--answers", type=int, default=0, help="dataset questions to answer and store per file (English ones first)")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    Q = {}
    if a.answers:
        from drawqa.solve import solve
        for line in open(os.path.join(ROOT, "junction-dataset-preview", "questions.jsonl")):
            q = json.loads(line)
            if q["type"] != "text":
                Q.setdefault(q["path"], []).append(q)
    for f in a.files:
        name = os.path.splitext(os.path.basename(f))[0]
        r = call("POST", "/api/sheets", open(f, "rb").read(), project=a.project, name=name, file_name=os.path.basename(f))
        sid = r["id"]
        n = right = 0
        t_all = 0.0
        if a.answers:
            rel = os.path.relpath(os.path.realpath(f), os.path.realpath(os.path.join(ROOT, "junction-dataset-preview")))
            qs = sorted(Q.get(rel, []), key=lambda q: not q["question"].isascii())
            seen = set()
            for q in qs:
                key = q["question"][:40]
                if key in seen or n >= a.answers:
                    continue
                seen.add(key)
                t0 = time.time()
                try:
                    res = solve(f, q["question"])
                except Exception as e:
                    continue
                sec = time.time() - t0
                t_all += sec
                keep = {k: res.get(k) for k in ("answer", "unit", "confidence", "explanation", "method", "honesty", "source_type") if k in res}
                keep["evidence_count"] = len(res.get("evidence") or [])
                call("POST", "/api/answers", json.dumps(dict(sheet_id=sid, question=q["question"], answer=keep, seconds=round(sec, 3), cost_usd=0.0)).encode(),
                     ctype="application/json")
                n += 1
                right += res.get("answer") == q["answer"] or (isinstance(res.get("answer"), (int, float)) and q["type"] != "count"
                                                              and abs(res["answer"] - q["answer"]) <= 0.02 * abs(q["answer"]))
        print(f"sheet {sid}: {name}  {r['width']}x{r['height']}  answers stored {n} ({right} match the dataset key, {t_all:.1f}s of engine time)")


if __name__ == "__main__":
    main()
