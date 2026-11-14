"""The answering engine (drawqa) on Modal, for questions the library has not answered yet.

The library web app (library/modal_app.py) queues a new question and spawns `answer` here. It finds the blueprint
(the dataset copy on the `bob-dataset` volume, so a BIM floor plan has its model and a Swiss plan its CAD file next
to it; otherwise the file stored in the library), runs drawqa.solve(), and posts the result back to the library,
which then shows it on the phone and keeps it for the next time.

    MODAL_PROFILE=oliver-universitapps modal deploy library/modal_engine.py

Language model for free wording: off unless BOB_LLM=1 is set in the `bob-engine` secret (it spends API credits).
"""
import os, pathlib

import modal

app = modal.App("bob-engine")
DATA = modal.Volume.from_name("bob-dataset", create_if_missing=True)
LIB = modal.Volume.from_name("bob-library", create_if_missing=True)
REPO = pathlib.Path(__file__).resolve().parent.parent
LIBRARY_URL = "https://oliver-universityapps--bob-library-web.us-east.modal.run"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("ifcopenshell", "pymupdf", "opencv-python-headless", "numpy<2", "pillow", "ezdxf", "anthropic")
    .add_local_dir(str(REPO / "drawqa"), "/root/app/drawqa", ignore=["__pycache__"])
    .add_local_dir(str(REPO / "data" / "predictions"), "/root/app/data/predictions")
)


def _find(file_name):
    """The dataset copy of a file, by name (data/<source>/<file>), or None."""
    for src in os.listdir("/ds/data"):
        p = f"/ds/data/{src}/{file_name}"
        if os.path.exists(p):
            return p
    return None


@app.function(image=image, volumes={"/ds": DATA, "/lib": LIB}, cpu=2, memory=4096, timeout=600, scaledown_window=600,
              secrets=[modal.Secret.from_dict({"BOB_PASSWORD": os.environ.get("BOB_PASSWORD", "123456789")})])
def answer(answer_id: int, sheet_id: int, file_name: str, question: str):
    import json, shutil, sys, time, urllib.request
    sys.path.insert(0, "/root/app")
    os.chdir("/root/app")
    from drawqa.solve import solve
    path, generic = _find(file_name), False
    if path is None:                                   # not from the dataset: the library's own copy, read as an unknown file
        LIB.reload()
        ext = os.path.splitext(file_name)[1] or ".pdf"
        path = f"/tmp/sheet{sheet_id}{ext}"
        shutil.copy(f"/lib/library/files/{sheet_id}/original", path)
        generic = True
    t0 = time.time()
    try:
        r = solve(path, question, generic=generic, llm=os.environ.get("BOB_LLM") == "1")
        keep = {k: r.get(k) for k in ("answer", "unit", "confidence", "explanation", "method", "honesty", "source_type") if k in r}
        keep["evidence_count"] = len(r.get("evidence") or [])
    except Exception as e:                             # an answer the phone can show, never a silent failure
        keep = dict(answer=None, honesty=dict(tier="cannot determine", basis=f"the engine failed on this blueprint: {type(e).__name__}"))
    sec = round(time.time() - t0, 3)
    body = json.dumps(dict(sheet_id=sheet_id, question=question, answer=keep, seconds=sec, cost_usd=0.0)).encode()
    req = urllib.request.Request(LIBRARY_URL + "/api/answers", data=body, method="POST",
                                 headers={"x-bob-password": os.environ["BOB_PASSWORD"], "content-type": "application/json"})
    urllib.request.urlopen(req, timeout=60).read()
    return dict(answer_id=answer_id, answer=keep.get("answer"), seconds=sec, generic=generic)
