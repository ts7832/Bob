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
    sheet = load_sheet(f, True)
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
            res = solve(resolve(path), question, path in OPENED, llm=True)  # free wording: a language model picks the tool
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
    print(f"Drawing QA demo on http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
