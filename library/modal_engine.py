"""The answering engine (drawqa) on Modal, for questions the library has not answered yet.

The library web app (library/modal_app.py) queues a new question and spawns `answer` here. It finds the blueprint
(the dataset copy on the `bob-dataset` volume, so a BIM floor plan has its model and a Swiss plan its CAD file next
to it; otherwise the file stored in the library), runs drawqa.solve(), and posts the result back to the library,
which then shows it on the phone and keeps it for the next time.

    MODAL_PROFILE=oliver-universitapps modal deploy library/modal_engine.py

Question reading: the pattern router and the Jev-style router (router3, library/jev_cascade.py) read every question; the
router's reading wins a disagreement at >= 90%. A question neither reads goes to Claude (llm_router: the model picks the
tool, code computes the number), Opus first and Haiku if Opus fails. Key and BOB_LLM=1: the `bob-anthropic` secret.
"""
import os, pathlib

import modal

JEV_MODEL = "router3"          # which Jev-style router on the bob-dataset volume (/ds/jev/<name>)
app = modal.App("bob-engine")
DATA = modal.Volume.from_name("bob-dataset", create_if_missing=True)
LIB = modal.Volume.from_name("bob-library", create_if_missing=True)
REPO = pathlib.Path(__file__).resolve().parent.parent
LIBRARY_URL = "https://oliver-universityapps--bob-library-web.us-east.modal.run"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("ifcopenshell", "pymupdf", "opencv-python-headless", "numpy<2", "pillow", "ezdxf", "anthropic",
                 "torch==2.5.1", "transformers==4.47.1", "sentencepiece",
                 "rapidocr==3.10.0", "onnxruntime", "shapely")          # OCR and outline post-processing run here too (detection on the GPU)
    .add_local_dir(str(REPO / "drawqa"), "/root/app/drawqa", ignore=["__pycache__"])
    .add_local_file(str(REPO / "library" / "jev_cascade.py"), "/root/app/jev_cascade.py")
    .add_local_file(str(REPO / "library" / "keypool.py"), "/root/app/keypool.py")
    .add_local_dir(str(REPO / "data" / "predictions"), "/root/app/data/predictions")
    # the detector and segmenter files: inference runs on the drawqa-infer GPU, but drawqa reads only when the weights
    # exist here (detect.available / segment.available) and takes the detector's thresholds from the file next to them.
    # Without them every uploaded drawing was read with no detections (0 doors, 0 windows), 15 Nov.
    .add_local_dir(str(REPO / "models"), "/root/app/models")
)


_INDEX = {}


def _find(file_name):
    """The dataset copy of a file, by name, anywhere under /ds/data (drawing sets keep their own folders), or None."""
    if not _INDEX:
        for root, _, files in os.walk("/ds/data"):
            for f in files:
                _INDEX.setdefault(f, os.path.join(root, f))
    return _INDEX.get(file_name)


def _library_copy(sheet_id, file_name):
    """The library's stored copy of an uploaded file (waits briefly for a just-written upload to reach the volume)."""
    import shutil, time
    src = f"/lib/library/files/{sheet_id}/original"
    for _ in range(6):
        LIB.reload()
        if os.path.exists(src):
            break
        time.sleep(2)
    path = f"/tmp/sheet{sheet_id}{os.path.splitext(file_name)[1] or '.pdf'}"
    shutil.copy(src, path)
    return path


@app.function(image=image, volumes={"/ds": DATA, "/lib": LIB}, cpu=2, memory=4096, timeout=600, scaledown_window=600,
              secrets=[modal.Secret.from_name("bob-site-password"), modal.Secret.from_name("bob-anthropic"), modal.Secret.from_name("bob-anthropic-pool")])
def answer(answer_id: int, sheet_id: int, file_name: str, question: str):
    import json, shutil, sys, time, urllib.request
    sys.path.insert(0, "/root/app")
    os.chdir("/root/app")
    import keypool
    keypool.install()                                  # Claude calls: Oliver's key, then kokoro's (bob-anthropic-pool)
    from drawqa.solve import solve
    path, generic = _find(file_name), False
    if path is None:                                   # not from the dataset: the library's own copy, read as an unknown file
        path, generic = _library_copy(sheet_id, file_name), True
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
                          detail=f"the engine asked a language model to read the wording (no pattern fitted, or a building-model question it "
                                 f"double-checks); it only chose the tool and its arguments (intent {json_intent(r)}), the number itself comes "
                                 f"from the step below. {qr.get('note') or ''}".strip()))
    steps.append(dict(stage="Answer produced", actor=r.get("method") or "engine",
                      detail=f"{len(ev)} evidence item(s) marked on the blueprint" + (f"; item confidence {confs[0]:.0%} to {confs[-1]:.0%}" if confs else ""),
                      confidence=r.get("confidence")))
    steps.append(dict(stage="Honesty", actor=h.get("tier") or "unknown", detail=h.get("basis") or "", confidence=r.get("confidence")))
    return dict(question=question, seconds=seconds, language_model_used=by != "pattern", steps=steps)


def _router_audit(r, how, fallback):
    """Both routers' readings, whether they agreed, and the combined confidence (engine x router, halved on a
    disagreement: measured to separate right from wrong answers better than the engine's confidence alone)."""
    if not how:
        return
    p, agree = how.get("probability"), how.get("agree")
    by = how.get("by") or ""
    pat = how.get("pattern_meaning")
    if by.startswith("jev router"):
        actor = f"Jev-style router ({JEV_MODEL})" + (" overrides the pattern router" if "override" in by else "")
    elif by == "pattern router":
        actor = "pattern router" + (", Jev-style router agrees" if agree else ", Jev-style router disagrees (pattern reading kept)" if agree is False else "")
    else:
        actor = "neither router could read it" + (" -> language model" if r.get("question_read", {}).get("by") == "language model" else "")
    detail = f"Jev-style router: {how.get('label')} at {p:.0%}" if p is not None else ""
    if pat:
        detail += f"; pattern router: {pat}"
    top = how.get("top") or []
    if len(top) > 1:
        detail += "; next: " + ", ".join(f"{l} {v:.0%}" for l, v in top[1:])
    r["audit"]["steps"].insert(1, dict(stage="Question read", actor=actor, detail=detail, confidence=p))
    r["audit"]["steps"] = [s for i, s in enumerate(r["audit"]["steps"]) if not (s["stage"] == "Question read" and i > 1 and s["actor"].startswith("pattern router (no"))]
    conf = r.get("confidence")
    if isinstance(conf, (int, float)) and p is not None:
        comb = conf * p * (0.5 if agree is False else 1.0)
        r["confidence_engine"] = conf
        r["confidence"] = round(comb, 4)
        r["audit"]["steps"].append(dict(stage="Overall confidence", actor="engine x question reading",
                                        detail=f"engine {conf:.0%} x reading {p:.0%}" + (" x 0.5 (the routers disagree)" if agree is False else ""),
                                        confidence=round(comb, 4)))
    r["audit"]["routers"] = dict(by=by, jev_label=how.get("label"), jev_probability=p, pattern_meaning=pat, agree=agree)
    if fallback:
        r["audit"]["steps"].append(dict(stage="Fallback", actor=fallback, detail="the main language model (Opus) did not answer, so Haiku read the question"))


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


@app.function(image=image, volumes={"/ds": DATA, "/lib": LIB}, cpu=2, memory=6144, timeout=600, scaledown_window=900, min_containers=1,
              secrets=[modal.Secret.from_name("bob-anthropic"), modal.Secret.from_name("bob-anthropic-pool")])
def solve_full(sheet_id: int, file_name: str, question: str):
    """Synchronous: the engine's whole result (evidence included, for the blueprint view) plus its audit trail."""
    import re, shutil, sys, time
    sys.path.insert(0, "/root/app")
    os.chdir("/root/app")
    import jev_cascade, keypool
    keypool.install()                                  # Claude calls: Oliver's key, then kokoro's (bob-anthropic-pool)
    jev_cascade.install("/ds/jev/" + JEV_MODEL)
    jev_cascade.use_claude_for_reader()           # table queries: Claude via our key (Aleks's reader is in his workspace)
    from drawqa.solve import solve
    from drawqa import llm_router
    path, generic = _find(file_name), False
    if path is None:
        path, generic = _library_copy(sheet_id, file_name), True
    calls = _watch_model_calls()
    use_llm = os.environ.get("BOB_LLM") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))
    t0 = time.time()
    fallback = None
    try:
        r = jev_cascade.answer(path, question, generic=generic, llm=use_llm)
        how = jev_cascade.last() or {}
        basis = str((r.get("honesty") or {}).get("basis") or "") + str(r.get("explanation") or "")
        if use_llm and r.get("answer") is None and how.get("by") == "nobody" and re.search(r"(?i)language model|anthropic|model call", basis):
            fallback, main_model = "claude-haiku-5-5", llm_router.MODEL     # Opus failed: try Haiku once, and say so
            llm_router.MODEL = fallback
            try:
                r = jev_cascade.answer(path, question, generic=generic, llm=True)
            finally:
                llm_router.MODEL = main_model
    except Exception as e:
        r = dict(answer=None, honesty=dict(tier="cannot determine", basis=f"the engine failed on this blueprint: {type(e).__name__}: {e}"[:300]))
    how = jev_cascade.last() or {}
    sec = round(time.time() - t0, 3)
    r = {k: v for k, v in r.items() if k != "cost"}
    r["audit"] = _audit(r, question, sec, generic)
    _router_audit(r, how, fallback)
    if calls:
        tin, tout = sum(c["in"] for c in calls), sum(c["out"] for c in calls)
        r["audit"]["language_model_used"] = True
        r["audit"]["paid_by"] = keypool.last()
        r["audit"]["model_calls"] = list(calls)
        r["audit"]["steps"].insert(2, dict(stage="Language model", actor=", ".join(sorted({c["model"] for c in calls})),
                                           detail=f"{len(calls)} call(s), {tin} tokens in, {tout} tokens out. It turned the wording into tool calls; "
                                                  f"the numbers are computed by code."))
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
