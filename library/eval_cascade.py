"""Whole-system test on Modal: the engine with the cascade router (patterns, then the Jev-style router) on every
dataset question that has a number for an answer, plus held-out rewordings of dataset questions.

One run gives both numbers: a question the pattern router reads gets the same answer with or without the cascade, so
"patterns only" = right only where the pattern router read the question.

    MODAL_PROFILE=oliver-universitapps modal run library/eval_cascade.py --rewordings /tmp/rewordings.json
Writes library/eval_out/cascade_<stamp>.jsonl (one line per question) and prints the summary.
"""
import json, os, pathlib, time

import modal

app = modal.App("bob-eval-cascade")
DATA = modal.Volume.from_name("bob-dataset")
REPO = pathlib.Path(__file__).resolve().parent.parent
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("ifcopenshell", "pymupdf", "opencv-python-headless", "numpy<2", "pillow", "ezdxf",
                 "torch==2.5.1", "transformers==4.47.1", "sentencepiece")
    .add_local_dir(str(REPO / "drawqa"), "/root/app/drawqa", ignore=["__pycache__"])
    .add_local_dir(str(REPO / "data" / "predictions"), "/root/app/data/predictions")
    .add_local_file(str(REPO / "library" / "jev_cascade.py"), "/root/app/jev_cascade.py")
)


def correct(pred, q):
    if pred is None or isinstance(pred, str):
        return False
    try:
        if q["type"] == "count":
            return float(pred) == float(q["answer"])
        return abs(float(pred) - float(q["answer"])) <= 0.02 * abs(float(q["answer"]))
    except (TypeError, ValueError):
        return False


@app.function(image=image, volumes={"/ds": DATA}, cpu=2, memory=6144, timeout=3600, max_containers=60)
def run_chunk(items: list):
    import sys
    sys.path.insert(0, "/root/app")
    os.chdir("/root/app")
    import jev_cascade
    jev_cascade.install("/ds/jev/" + os.environ.get("JEV_MODEL", "router2"))
    from drawqa.solve import solve
    out = []
    for q in items:
        t0 = time.time()
        try:
            r = solve("/ds/" + q["path"], q["question"])
            ans, conf, tier, err = r.get("answer"), r.get("confidence"), (r.get("honesty") or {}).get("tier"), None
        except Exception as e:
            ans, conf, tier, err = None, None, "error", f"{type(e).__name__}: {e}"[:160]
        how = jev_cascade.last() or {}
        out.append(dict(id=q["id"], set=q["set"], source=q["path"].split("/")[1], kind=q["kind"], type=q["type"], answer=ans, key=q["answer"],
                        ok=correct(ans, q), read_by=how.get("by"), jev_label=how.get("label"), jev_p=how.get("probability"),
                        conf=conf, tier=tier, err=err, seconds=round(time.time() - t0, 3)))
    return out


@app.local_entrypoint()
def main(rewordings: str = "", limit: int = 0):
    import collections, sys
    sys.path.insert(0, str(REPO / "library"))
    meaning = json.load(open(REPO / "library" / "eval_meanings.json"))       # question -> meaning label (kind)
    Q = [json.loads(l) for l in open(REPO / "junction-dataset-preview" / "questions.jsonl")]
    preview_scans = {json.loads(l)["path"] for l in open(REPO / "data" / "predictions" / "yolo_c26l_tta.jsonl")}
    items = []
    for i, q in enumerate(Q):
        src = q["path"].split("/")[1]
        if q["type"] == "text" or src not in ("swiss_dwellings", "ifc_models", "opening_design", "cubicasa5k"):
            continue
        if src == "cubicasa5k" and os.path.basename(q["path"]) not in preview_scans:
            continue                                         # scans: only the 241 test plans have detector output
        items.append(dict(id=f"d{i}", set="dataset", path=q["path"], question=q["question"], answer=q["answer"], type=q["type"],
                          kind=meaning.get(q["question"], "unlabelled")))
    if rewordings:
        for j, r in enumerate(json.load(open(rewordings))):
            items.append(dict(id=f"r{j}", set="reworded:" + r.get("generator", "?"), path=r["path"], question=r["question"], answer=r["answer"],
                              type=r["type"], kind=r["label"]))
    if limit:
        items = items[:limit]
    groups = collections.defaultdict(list)                   # one drawing per container stretch: the engine caches a converted sheet
    for it in items:
        groups[it["path"]].append(it)
    chunks, cur = [], []
    for path in sorted(groups):
        cur += groups[path]
        if len(cur) >= 700:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    print(f"{len(items)} questions on {len(groups)} drawings in {len(chunks)} chunks")
    t0 = time.time()
    rows = [r for part in run_chunk.map(chunks) for r in part]
    os.makedirs(REPO / "library" / "eval_out", exist_ok=True)
    path = REPO / "library" / "eval_out" / f"cascade_{time.strftime('%m%d_%H%M')}.jsonl"
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} answered in {time.time() - t0:.0f} s -> {path}")
