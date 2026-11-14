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


READERS = {"vector-dxf": "CAD reader: the DXF file's own layers (walls, doors, windows, rooms), exact",
           "vector-pdf": "PDF reader: the page's own text and vector linework at its printed scale",
           "raster": "image pipeline: YOLO26 element detector (c26l) and wall/room segmenter (seg1) on the image, plus OCR for printed text",
           "ifc": "building-model reader: IfcOpenShell queries on the IFC model behind this plan, exact"}


def _audit(r, question, seconds, generic):
    """The answer's process as steps a person can check: who read the file, who read the question, what produced the
    number, how sure, and where a language model was used and why."""
    st, qr, h = r.get("source_type") or "", r.get("question_read") or {}, r.get("honesty") or {}
    ev = r.get("evidence") or []
    confs = sorted(float(e["conf"]) for e in ev if isinstance(e, dict) and isinstance(e.get("conf"), (int, float)))
    steps = [dict(stage="Blueprint read", actor=READERS.get(st, f"reader for {st or 'this file'}"),
                  detail=("read as an unknown file (no dataset shortcuts)" if generic else "dataset copy with its companion files (model / CAD file)"))]
    by = qr.get("by") or "pattern"
    if by == "pattern":
        steps.append(dict(stage="Question read", actor="pattern router (no language model)", detail=f"{qr.get('note') or 'matched a known question pattern'}; intent {json_intent(r)}"))
    else:
        steps.append(dict(stage="Question read", actor=f"language model ({qr.get('model') or 'Claude'})",
                          detail=f"used because no known pattern matched this wording; it only chose the tool and its arguments "
                                 f"(intent {json_intent(r)}), the number itself comes from the step below. {qr.get('note') or ''}".strip()))
    steps.append(dict(stage="Answer produced", actor=r.get("method") or "engine",
                      detail=f"{len(ev)} evidence item(s) marked on the blueprint" + (f"; item confidence {confs[0]:.0%} to {confs[-1]:.0%}" if confs else ""),
                      confidence=r.get("confidence")))
    steps.append(dict(stage="Honesty", actor=h.get("tier") or "unknown", detail=h.get("basis") or "", confidence=r.get("confidence")))
    return dict(question=question, seconds=seconds, language_model_used=by != "pattern", steps=steps)


def _watch_model_calls():
    """Record every Claude call the engine makes (model, tokens) for the audit trail. Returns the (cleared) list."""
    try:
        from anthropic.resources.messages import Messages
    except Exception:
        return []
    if not getattr(Messages.create, "_bob", False):
        orig, calls = Messages.create, []

        def create(self, *a, **kw):
            m = orig(self, *a, **kw)
            u = getattr(m, "usage", None)
            calls.append({"model": kw.get("model") or getattr(m, "model", "?"), "in": getattr(u, "input_tokens", 0) or 0, "out": getattr(u, "output_tokens", 0) or 0})
            return m
        create._bob, create._bob_calls = True, calls
        Messages.create = create
    Messages.create._bob_calls.clear()
    return Messages.create._bob_calls


def json_intent(r):
    i = r.get("intent") or {}
    return ", ".join(f"{k}={v}" for k, v in i.items() if v not in (None, "", [])) or "unknown"


@app.function(image=image, volumes={"/ds": DATA, "/lib": LIB}, cpu=2, memory=4096, timeout=600, scaledown_window=900, min_containers=1)
def solve_full(sheet_id: int, file_name: str, question: str):
    """Synchronous: the engine's whole result (evidence included, for the blueprint view) plus its audit trail."""
    import shutil, sys, time
    sys.path.insert(0, "/root/app")
    os.chdir("/root/app")
    from drawqa.solve import solve
    path, generic = _find(file_name), False
    if path is None:
        LIB.reload()
        path = f"/tmp/sheet{sheet_id}{os.path.splitext(file_name)[1] or '.pdf'}"
        shutil.copy(f"/lib/library/files/{sheet_id}/original", path)
        generic = True
    calls = _watch_model_calls()
    t0 = time.time()
    try:
        r = solve(path, question, generic=generic, llm=os.environ.get("BOB_LLM") == "1")
    except Exception as e:
        r = dict(answer=None, honesty=dict(tier="cannot determine", basis=f"the engine failed on this blueprint: {type(e).__name__}: {e}"[:300]))
    sec = round(time.time() - t0, 3)
    r = {k: v for k, v in r.items() if k != "cost"}
    r["audit"] = _audit(r, question, sec, generic)
    if calls:
        tin, tout = sum(c["in"] for c in calls), sum(c["out"] for c in calls)
        r["audit"]["language_model_used"] = True
        r["audit"]["model_calls"] = list(calls)
        r["audit"]["steps"].insert(2, dict(stage="Language model", actor=", ".join(sorted({c["model"] for c in calls})),
                                           detail=f"{len(calls)} call(s), {tin} tokens in, {tout} tokens out. Used because no known question pattern "
                                                  f"matched; it turned the wording into tool calls, the numbers are computed by code."))
    return dict(result=r, seconds=sec)


@app.function(image=image, cpu=1, memory=1024, timeout=60, scaledown_window=900, min_containers=1)
def split(question: str):
    """A question with several parts -> its parts, each one a question the engine's patterns can read on its own.
    "How many doors and windows are on this floor?" -> doors, windows. Anything else stays one question."""
    import re, sys
    sys.path.insert(0, "/root/app")
    from drawqa import router
    q = question.strip()
    parts = []
    qs = [p.strip() + "?" for p in re.split(r"\?\s*", q) if p.strip()]           # several questions in one message
    for one in qs:
        m = re.match(r"(?i)^(how many|what is the total number of|count the)\s+(.+?)\s+(are|is|on|in|shown|drawn)\b(.*)$", one)
        nouns = re.split(r"\s*,\s*|\s+and\s+|\s*\+\s*", m.group(2)) if m else []
        if m and len(nouns) > 1:
            rest = re.sub(r"(?i)\s*\b(in total|altogether|together|combined)\b", "", m.group(4))
            cand = [f"{m.group(1)} {n} {m.group(3)}{rest}" for n in nouns if n]
            if all(router.parse(c)["intent"] != "unknown" for c in cand):
                parts += cand
                continue
        m = re.split(r"(?i)\s+and\s+(?=(?:how many|what|which|where)\b)", one)  # "... and what is ..."
        parts += [p if p.endswith("?") else p + "?" for p in m]
    total = bool(re.search(r"(?i)\b(in total|total|together|combined|altogether|sum)\b", q))
    return dict(parts=parts if len(parts) > 1 else [q], total=total)
