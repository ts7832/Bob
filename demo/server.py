"""Demo server (stdlib only): python demo/server.py  ->  http://localhost:8000"""
import json, os, sys, mimetypes, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(ROOT, "junction-dataset-preview")
sys.path.insert(0, ROOT)
from drawqa.solve import solve, load_sheet, source_of  # noqa: E402
from drawqa import scan  # noqa: E402

QUESTIONS = [json.loads(l) for l in open(os.path.join(DATA, "questions.jsonl"))]
SAMPLES = [
    ("data/swiss_dwellings/site1102_bldg2242_floor10281.pdf", "Swiss floor plan (Italian labels)"),
    ("data/swiss_dwellings/site11692_bldg18240_floor48820.pdf", "Swiss floor plan (German, no printed areas)"),
    ("data/swiss_dwellings/site1138_bldg2292_floor10584.pdf", "Swiss floor plan (French, large)"),
    ("data/ifc_models/bsc-esplanades_1807-ep-ar-v18_plan-04-4-korrus.pdf", "BIM plan: Esplanades, 4th floor (Estonian)"),
    ("data/ifc_models/bsc-molio_molio-with-uris_plan-02-klder.pdf", "BIM plan: Molio, basement (Danish)"),
    ("data/cubicasa5k/colorful-11397.png", "Scanned plan (Finnish)"),
    ("data/cubicasa5k/colorful-11400.png", "Scanned plan (Finnish)"),
]


def samples():
    preds = scan.predictions()
    out = []
    for path, title in SAMPLES:
        if not os.path.exists(os.path.join(DATA, path)):
            continue
        if source_of(path) == "cubicasa5k" and os.path.basename(path) not in preds:
            continue
        qs, seen = [], set()
        for q in QUESTIONS:
            if q["path"] == path and q["type"] != "text":
                key = q["question"].split("?")[0][:25]
                if key not in seen:
                    seen.add(key)
                    qs.append(dict(question=q["question"], answer=q["answer"], unit=q["unit"]))
        out.append(dict(path=path, title=title, source=source_of(path), questions=qs[:14]))
    return out


OPENED = {}  # "file:<abs path>" -> abs path, for files opened through /api/open or /api/upload
UPLOADS = os.path.join(ROOT, "data", "uploads")  # dropped files are kept here (git-ignored)
MAX_UPLOAD = 80 * 1024 * 1024


def save_upload(name, data):
    """Bytes dropped on the page -> a file under data/uploads (content hash in the name, so a re-drop reuses it)."""
    import hashlib, re
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(name or ""))[-80:].lstrip(".") or "drawing"
    os.makedirs(UPLOADS, exist_ok=True)
    f = os.path.join(UPLOADS, hashlib.sha1(data).hexdigest()[:10] + "_" + safe)
    if not os.path.exists(f):
        with open(f, "wb") as out:
            out.write(data)
    return f


def resolve(path):
    return OPENED[path] if path in OPENED else os.path.join(DATA, path)


def open_file(f):
    """Any local file -> unified sheet (drawqa.convert) -> an entry the page can show and ask questions about."""
    f = os.path.abspath(os.path.expanduser(f))
    if not os.path.isfile(f):
        raise FileNotFoundError(f)
    key = "file:" + f
    OPENED[key] = f
    cad_info = None
    sheet = load_sheet(f, True)
    if sheet.get("source_type") == "unknown" and str(sheet.get("kind", "")).startswith(("dwg", "cad dxf")):
        # a DWG, or a DXF the exact reader does not know: draw it onto a PDF page (drawqa/cad.py) and read that page
        from drawqa import cad
        try:
            cad_info = cad.to_pdf(f)
        except cad.CadError as e:
            OPENED.pop(key, None)
            raise ValueError(f"This CAD file could not be read: {e}.")
        OPENED[key] = cad_info["pdf"]
        sheet = load_sheet(cad_info["pdf"], True)
    if sheet.get("source_type") == "unknown":
        OPENED.pop(key, None)
        raise ValueError(sheet.get("note") or "This file type cannot be read yet.")
    qs = ["How many doors are shown on this floor plan?", "How many windows are shown on this floor plan?",
          "How many toilet bowls (WC seats) are drawn on this floor plan?", "How many sinks and wash basins are drawn on this floor plan?",
          "How many showers and bathtubs are drawn on this floor plan in total?"]
    if sheet.get("source_type") == "raster":
        qs += ["How many bedrooms (makuuhuone, usually marked MH) are on this floor plan?", "How many floors (storeys) does this floor plan show?"]
    for r in [r for r in sheet.get("rooms", []) if r.get("number")][:3]:
        qs.append(f"What is the net floor area of room {r['number']} in m²?")
    scale = (sheet.get("scale") or {}).get("source", "")
    title = f"{os.path.basename(f)} ({sheet.get('kind', sheet.get('source_type'))})"
    if cad_info:
        title = f"{os.path.basename(f)} ({'DWG' if cad_info['was_dwg'] else 'DXF'} drawn to a page: {cad_info['entities']} entities, {cad_info['texts']} texts)"
        if cad_info.get("m_per_pt"):
            scale = f"exact, from the CAD file's own units ({cad_info['units']}): 1 page point = {cad_info['m_per_pt'] * 1000:.2f} mm (not yet used for measuring)"
    return dict(path=key, title=title, source=sheet.get("kind", "file"), note=f"scale: {scale}" if scale else "",
                questions=[dict(question=q, answer=None, unit=None) for q in qs])


def plan(path):
    """Everything the converter found on the sheet, as shapes the page can draw over the drawing."""
    sheet = load_sheet(resolve(path), path in OPENED)
    kind = sheet.get("kind") or sheet.get("source_type") or "file"
    scale = (sheet.get("scale") or {}).get("source") or ""
    if (sheet.get("render") or {}).get("type") not in ("image", "pdf"):  # CAD and BIM are drawn from their own geometry
        return dict(drawable=False, kind=kind, scale=scale, items=[], note=sheet.get("note") or "")
    items = []

    def shape(e):
        if e.get("polygon"):
            return dict(pts=[[round(x, 1), round(y, 1)] for x, y in e["polygon"]])
        return dict(bbox=[round(v, 1) for v in e["bbox"]]) if e.get("bbox") else None

    def add(e, type_, how, label=None, **extra):
        sh = shape(e)
        if sh:
            items.append(dict(type=type_, how=how, label=label, conf=e.get("conf"), **sh, **extra))

    for e in list(sheet.get("openings", [])) + list(sheet.get("fixtures", [])):
        add(e, e.get("cls") or e.get("type") or "element", "linework" if e.get("exact") else "detector")
    for r in sheet.get("rooms", []):
        if r.get("cls"):  # room type found by the detector
            add(r, r["cls"], "inferred" if r.get("type_source") else "outline" if r["cls"] == "unlabelled" else "detector",
                r.get("name"), area_m2=r.get("area_m2"), area_source=r.get("area_source"), type_source=r.get("type_source"))
        else:  # room read from the sheet's own text and outlines
            label = " ".join(str(v) for v in (r.get("number"), r.get("name")) if v)
            add(r, "room", "read", label, area_m2=r.get("area_m2"), area_source=r.get("area_source"))
    for wl in sheet.get("walls", []):  # wall segments found by the segmentation model (images only)
        add(wl, "wall", "detector")
    for f in sheet.get("floors", []):
        add(f, "floor", "detector")
    for u in sheet.get("units_list", []):
        add(u, "apartment", "read", u.get("label"))
    names = None
    if sheet.get("source_type") == "raster":  # room names read off the image by the vision model, if that was run
        from drawqa import reader
        names = reader.cached(resolve(path))
    return dict(drawable=True, kind=kind, scale=scale, items=items, texts=len(sheet.get("texts", [])), names=names,
                can_read_names=sheet.get("source_type") == "raster",
                linework=sheet.get("linework_notes") or {}, fit=sheet.get("detector_fit") or {})


def sheet_json(path):
    """The unified sheet itself (format drawqa-sheet/1), for download."""
    sheet = load_sheet(resolve(path), path in OPENED)
    return json.dumps(sheet, indent=1, ensure_ascii=False,
                      default=lambda o: list(o) if isinstance(o, (set, tuple)) else str(o)).encode()


_TABLES = {}


def sheet_tables(f):
    """Tables (schedules) on a PDF sheet, read from its own words and ruled lines (drawqa/schedule.py)."""
    if f not in _TABLES:
        from drawqa import schedule
        with open(f, "rb") as fh:
            is_pdf = fh.read(5).startswith(b"%PDF")
        _TABLES[f] = schedule.tables(f)[:8] if is_pdf else []
    return _TABLES[f]


def names_a_table(f, question):
    """True when the question uses the title of a table that is on this sheet (at least two of its words, most of them)."""
    import re
    try:
        tabs = sheet_tables(f)
    except Exception:
        return False
    q = set(re.findall(r"[a-z0-9]+", question.lower()))
    for t in tabs:
        words = [w for w in re.findall(r"[a-z]+", t["title"].lower()) if len(w) > 2]
        if len(words) >= 2 and len(q & set(words)) >= 2 and len(q & set(words)) >= 0.6 * len(set(words)):
            return True
    return False


def table_answer(f, question):
    """A question about a schedule: a language model writes the query, code runs it on the table read from the PDF."""
    from drawqa import schedule
    tabs = sheet_tables(f)
    if not tabs:
        return None
    plan, note = schedule.plan(question, tabs)
    if plan:
        res = schedule.run(tabs, plan)
        read = dict(by="language model", note=f"{note}: {res['query']['op']} on '{res['query']['table']}' where {res['query']['where']}"
                    + (f", column {', '.join(res['query']['columns'])}" if res["query"]["columns"] else ""))
    else:  # no model: only what can be tied to the table's own values without guessing
        intent = schedule.guess(question, tabs)
        if intent is None:
            return dict(answer=None, unit=None, evidence=[], intent=dict(intent="schedule"), method="table query", confidence=None,
                        explanation=f"This sheet has {len(tabs)} table(s) ({', '.join(repr(t['title'][:30]) for t in tabs[:4])}), but the question could "
                                    f"not be turned into a query: {note}.",
                        honesty=dict(tier="cannot determine", deterministic=None, basis=note))
        res = schedule.answer(tabs, intent)
        read = dict(by="matched to the table's own values", note="no language model was used")
    res.setdefault("evidence", [])
    res.setdefault("unit", None)
    ok = res.get("answer") is not None
    res.update(intent=dict(intent="schedule", **(res.get("query") or {})), method="table query", source_type="vector-pdf", question_read=read,
               confidence=0.9 if ok else None,
               honesty=dict(tier="read" if ok else "cannot determine", deterministic=True if ok else None,
                            basis=(res.get("schedule_source") or "") + ". The cells are the sheet's own text and the arithmetic is done by code; "
                                  "which query answers the question was decided by " + read["by"] + ", so check the query shown."))
    return res


def page_png(path):
    """Rendered page of an opened PDF, as PNG bytes (same pixels the detector and the evidence boxes use)."""
    import cv2
    import pymupdf
    from drawqa import pdfsheet
    return cv2.imencode(".png", pdfsheet.render(pymupdf.open(resolve(path))[0]))[1].tobytes()


def render(path):
    sheet = load_sheet(resolve(path), path in OPENED)
    r = dict(sheet["render"])
    if r["type"] == "pdf":
        r = dict(type="image", width=r["width"], height=r["height"], url="/page?path=" + quote(path))
    elif r["type"] == "image" and path in OPENED:
        r["url"] = "/file?path=" + quote(path)
    if r["type"] == "svg":
        r["svg"] = open(r.pop("svg_path"), encoding="utf-8").read()
    if r["type"] == "image" and "url" not in r:
        r["url"] = "/data/" + path
    r["source_type"] = sheet["source_type"]
    note = sheet.get("note") or sheet.get("model") or ""
    if (sheet.get("scale") or {}).get("source"):
        note = (note + " · " if note else "") + "scale: " + sheet["scale"]["source"]
    r["note"] = ("BIM model: " + os.path.basename(note)) if note.endswith(".ifc") else note
    return r


# ---- picture-first app (/app): upload -> reading (models + Claude) -> questions to the answer agent (Kimi) ----
import threading, time  # noqa: E402

JOBS = {}  # job id -> dict(path, name, stage, detail, started, steps, layer, error)
STAGES = {"queued": "waiting", "picture": "Drawing the page as a picture", "models": "Our trained models: doors, windows, fixtures, walls, rooms, text",
          "claude": "Claude (Opus, medium effort) is reading the whole drawing", "done": "Ready", "error": "Failed"}


def start_reading(f, name):
    from drawqa import layer as LY
    jid = os.path.basename(f).split("_")[0]
    if jid in JOBS and JOBS[jid]["stage"] != "error":
        return jid
    J = JOBS[jid] = dict(path=f, name=name or os.path.basename(f), stage="queued", detail="", started=time.time(), steps=[])

    def progress(stage, detail):
        J.update(stage=stage, detail=detail)
        J["steps"].append(dict(stage=stage, at=round(time.time() - J["started"], 1)))

    def run():
        # 1. our models (seconds): the drawing is ready for questions as soon as they are done
        # 2. Claude's full reading (minutes) goes on in the background and replaces the layer when it finishes
        try:
            cached = LY.ingest(f, discover=False)  # the full reading when it is cached, else the models' reading
            full = (cached.get("discovery") or {}).get("status") == "done"
            L = cached if full else LY.ingest(f, discover=False, progress=progress)
            if not L.get("picture"):
                raise ValueError("; ".join(L.get("notes") or []) or "this file could not be drawn as a picture")
            J.update(stage="done", detail="Ready", layer=L, seconds=round(time.time() - J["started"], 1),
                     reading="done" if full else "running", reading_started=time.time())
        except Exception as e:
            traceback.print_exc()
            J.update(stage="error", detail=str(e))
            return
        if full:
            return
        try:
            L2 = LY.ingest(f, discover=True, progress=lambda st, d: J.update(reading_detail=d))
            ok = (L2.get("discovery") or {}).get("status") == "done"
            J.update(layer=L2 if L2.get("picture") else J["layer"], reading="done" if ok else "failed",
                     reading_seconds=round(time.time() - J["reading_started"], 1))
        except Exception as e:
            traceback.print_exc()
            J.update(reading="failed", reading_detail=str(e))
    threading.Thread(target=run, daemon=True).start()
    return jid


def job_status(jid):
    J = JOBS[jid]
    out = dict(id=jid, name=J["name"], stage=J["stage"], label=STAGES.get(J["stage"], J["stage"]), detail=J.get("detail"),
               elapsed=round(time.time() - J["started"], 1), steps=J["steps"], reading=J.get("reading"),
               reading_elapsed=round(time.time() - J.get("reading_started", time.time()), 1), reading_seconds=J.get("reading_seconds"))
    L = J.get("layer")
    if L:
        W, Hh = L["width"], L["height"]
        out.update(width=W, height=Hh, display_scale=min(1.0, 3200 / max(W, Hh)), seconds=J.get("seconds"),
                   scale=dict(m_per_px=L.get("m_per_px"), source=L.get("scale_source")), notes=L.get("notes") or [],
                   discovery={k: v for k, v in (L.get("discovery") or {}).items() if k != "report"},
                   classes=[dict(name=c["name"], count=c.get("count", len(c.get("items") or [])), source=c.get("source")) for c in L.get("classes", [])])
    return out


def job_picture(jid):
    J = JOBS[jid]
    if "jpg" not in J:
        from PIL import Image
        import io
        Image.MAX_IMAGE_PIXELS = None
        im = Image.open(J["layer"]["picture"]).convert("RGB")
        s = min(1.0, 3200 / max(im.size))
        if s < 1:
            im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
        J["jpg"] = buf.getvalue()
    return J["jpg"]


def job_layer(jid):
    keep = ("id", "bbox", "polygon", "polyline", "point", "label", "type", "tag", "name", "area_m2", "face", "perimeter_m")
    return [dict(name=c["name"], source=c.get("source"), items=[{k: it[k] for k in keep if k in it} for it in c.get("items") or []],
                 **({"outline": c["outline"]} if c.get("outline") else {}), **({"hidden": True} if c.get("hidden") else {}))
            for c in JOBS[jid]["layer"].get("classes", [])]


def job_ask(jid, question):
    from drawqa.ask import ask
    J = JOBS[jid]
    L = J["layer"]
    busy = J.get("reading") == "running"
    if busy:  # tell the answer agent what this reading is and is not (yet)
        L = dict(L, notes=(L.get("notes") or []) + [
            "Claude's full reading of this drawing is still running: this layer holds only our trained models' classes "
            "(doors, windows, fixtures, walls, rooms, text). If the question needs anything else, answer 'cannot determine' "
            "and say the full reading is still in progress."])
    r = ask(J["path"], question, layer=L)
    out = {k: r.get(k) for k in ("answer", "unit", "tier", "basis", "evidence", "notes", "route", "model", "seconds")}
    if busy:
        out["notes"] = (out.get("notes") or []) + [f"Claude is still reading the whole drawing ({round(time.time() - J['reading_started'])} s so far): "
                                                   "furniture and other details are added when it finishes; answered from our models' reading"]
    return out


def warm_kimi():
    try:
        from drawqa import llm
        print(f"Kimi on Modal is warm ({llm.warm()} s)")
    except Exception as e:
        print(f"Kimi warm-up failed: {e}")


def warm_models():
    try:
        from drawqa import remote
        if remote.enabled():
            print(f"Our models on the Modal GPU are warm ({remote.warm()} s)")
    except Exception as e:
        print(f"Model service warm-up failed (reading will run on this computer): {e}")


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/":
                return self._send(200, open(os.path.join(ROOT, "demo", "index.html"), "rb").read(), "text/html; charset=utf-8")
            if u.path == "/app":
                return self._send(200, open(os.path.join(ROOT, "demo", "app.html"), "rb").read(), "text/html; charset=utf-8")
            if u.path == "/api/v2/status":
                return self._send(200, job_status(parse_qs(u.query)["id"][0]))
            if u.path == "/api/v2/picture":
                return self._send(200, job_picture(parse_qs(u.query)["id"][0]), "image/jpeg")
            if u.path == "/api/v2/layer":
                return self._send(200, job_layer(parse_qs(u.query)["id"][0]))
            if u.path == "/api/samples":
                return self._send(200, samples())
            if u.path == "/api/open":
                return self._send(200, open_file(parse_qs(u.query)["file"][0]))
            if u.path == "/page":
                return self._send(200, page_png(parse_qs(u.query)["path"][0]), "image/png")
            if u.path == "/file":
                f = OPENED[parse_qs(u.query)["path"][0]]  # only files opened through /api/open
                return self._send(200, open(f, "rb").read(), mimetypes.guess_type(f)[0] or "application/octet-stream")
            if u.path == "/api/render":
                return self._send(200, render(parse_qs(u.query)["path"][0]))
            if u.path == "/api/plan":
                return self._send(200, plan(parse_qs(u.query)["path"][0]))
            if u.path == "/api/tables":
                tabs = sheet_tables(resolve(parse_qs(u.query)["path"][0]))
                return self._send(200, [dict(title=t["title"], columns=t["columns"], rows=t["rows"]) for t in tabs])
            if u.path == "/api/sheet":
                return self._send(200, sheet_json(parse_qs(u.query)["path"][0]), "application/json; charset=utf-8")
            if u.path.startswith("/data/"):
                f = os.path.abspath(os.path.join(DATA, u.path[len("/data/"):]))
                if not f.startswith(DATA) or not f.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                    return self._send(403, {"error": "forbidden"})
                return self._send(200, open(f, "rb").read(), mimetypes.guess_type(f)[0] or "application/octet-stream")
            return self._send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/v2/upload":  # body = the file's bytes, ?name= its file name -> starts the reading
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_UPLOAD:
                    return self._send(413, {"error": f"file is empty or larger than {MAX_UPLOAD // 2 ** 20} MB"})
                name = (parse_qs(u.query).get("name") or [""])[0]
                return self._send(200, {"id": start_reading(save_upload(name, self.rfile.read(n)), name)})
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
        if u.path == "/api/v2/ask":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if JOBS.get(body["id"], {}).get("stage") != "done":
                    return self._send(409, {"error": "the drawing is still being read"})
                return self._send(200, job_ask(body["id"], body["question"].strip()))
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
        if u.path == "/api/upload":  # body = the file's bytes, ?name= its file name
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_UPLOAD:
                    return self._send(413, {"error": f"file is empty or larger than {MAX_UPLOAD // 2 ** 20} MB"})
                name = (parse_qs(u.query).get("name") or [""])[0]
                d = open_file(save_upload(name, self.rfile.read(n)))
                if name:
                    d["title"] = d["title"].replace(os.path.basename(OPENED[d["path"]]), os.path.basename(name), 1)
                return self._send(200, d)
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
        if u.path == "/api/read":  # run the room-name reader (vision model on a Modal GPU) on an opened image
            try:
                from drawqa import reader
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                return self._send(200, reader.read_rooms(resolve(body["path"])))
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
        if u.path != "/api/ask":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            path, question = body["path"], body["question"].strip()
            res = None
            if names_a_table(resolve(path), question):  # "…in the door schedule…": the table on the sheet answers, not the drawing
                res = table_answer(resolve(path), question)
                if res is not None and res.get("answer") is None:
                    res = None
            if res is None:
                res = solve(resolve(path), question, path in OPENED, llm=True)  # free wording: a language model picks the tool
                if res.get("answer") is None:  # not answerable from the drawing itself: maybe a table still can
                    res = table_answer(resolve(path), question) or res
            truth = next((q for q in QUESTIONS if q["path"] == path and q["question"].strip() == question), None)
            if truth is not None:
                res["dataset_answer"] = truth["answer"]
            return self._send(200, res)
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": str(e)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Drawing QA demo on http://localhost:{port}  (new flow: http://localhost:{port}/app)")
    threading.Thread(target=warm_kimi, daemon=True).start()
    threading.Thread(target=warm_models, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
