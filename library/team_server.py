"""Kokoro's team page, with questions answered on Modal (Oliver's workspace) and kept in the library.

    PORT=8770 python library/team_server.py        (from the repo root, with the project venv)

Runs tools/ui_server.py unchanged, with three additions:
  - /bob/onebit.css and /bob/onebit.js are served (the One-bit design, the QR button and the audit trail);
  - the engine call behind /api/ask goes to the library on Modal (POST /api/solve): the blueprint is registered in the
    library the first time it is asked about, each question is split into its parts, and every answer and part is kept,
    so asking it (or one of its parts) again is answered from the library;
  - /bob/lib/<path> passes calls to the library (corrections, notes to Opus, history, savings, revisions) with the
    workspace password added here; /bob/sid?path= gives an open file's library id (and a revision guess after upload);
  - /bob/tools.js and /bob/tools.css: corrections, circle-a-problem, history, money saved, revisions (ui/bob/).
Rendering, plans, tables and costs stay on this laptop, as in tools/ui_server.py.
"""
import json, os, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, REPO)
import ui_server  # noqa: E402

URL = os.environ.get("BOB_LIBRARY", "https://oliver-universityapps--bob-library-web.us-east.modal.run")
PW = os.environ.get("BOB_PASSWORD", "123456789")
PROJECT = os.environ.get("BOB_PROJECT", "Team page")
LOCAL_SOURCES = ()          # everything on Modal now: scans use the live detector there (drawqa-infer)
_ids = {}
_suggest = {}               # sheet id -> the library's "this looks like a newer version of ..." from its upload


def call(method, path, body=None, ctype="application/json", timeout=600, **params):
    url = URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"x-bob-password": PW, "content-type": ctype})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def sheet_id(path):
    """The library sheet for this file, registered (uploaded) the first time."""
    name = os.path.basename(path)
    if name in _ids:
        try:                                   # still in the library? (a deleted sheet must not be used again)
            call("GET", f"/api/sheets/{_ids[name]}", timeout=20)
            return _ids[name]
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            _ids.pop(name, None)
    try:
        sid = call("GET", "/api/find", file_name=name)["id"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        up = call("POST", "/api/sheets", open(path, "rb").read(), "application/octet-stream", project=PROJECT,
                  name=os.path.splitext(name)[0], file_name=name)
        sid = up["id"]
        if up.get("revision_suggestion"):
            _suggest[sid] = up["revision_suggestion"]
    _ids[name] = sid
    return sid


def _floors(path):
    import re
    path = path[5:] if path.startswith("file:") else path
    full = path if os.path.isabs(path) else os.path.join(REPO, "junction-dataset-preview", path)
    d, name = os.path.split(full)
    m = re.match(r"^(.*)_plan-(\d+)-(.+)\.pdf$", name)
    if not m or not os.path.isdir(d):
        return dict(floors=[])
    fl = []
    for f in sorted(os.listdir(d)):
        mm = re.match(r"^" + re.escape(m.group(1)) + r"_plan-(\d+)-(.+)\.pdf$", f)
        if mm:
            fl.append(dict(n=int(mm.group(1)), name=mm.group(2).replace("-", " "), path=os.path.join(d, f), current=f == name))
    fl.sort(key=lambda x: -x["n"])
    return dict(building=m.group(1).split("_", 1)[-1].replace("-", " "), floors=fl if len(fl) > 1 else [])


UPLOADS = os.path.join(REPO, "data", "uploads", "projects")
_paths = {}                 # library sheet id -> local file
_dataset = {}


def _slug(s):
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:80] or "project"


def _local(sid, file_name):
    if sid in _paths and os.path.exists(_paths[sid]):
        return _paths[sid]
    if not _dataset:                            # the dataset's own files, by name (dataset blueprints open with their companions)
        for root, _, fs in os.walk(os.path.join(REPO, "junction-dataset-preview", "data")):
            for f in fs:
                _dataset.setdefault(f, os.path.join(root, f))
    if file_name in _dataset:
        _paths[sid] = _dataset[file_name]
        return _paths[sid]
    d = os.path.join(UPLOADS, "library", str(sid))            # its own folder: the file keeps its library name (corrections find it)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, os.path.basename(file_name) or "sheet")
    if not os.path.exists(path):
        req = urllib.request.Request(URL + f"/api/sheets/{sid}/original", headers={"x-bob-password": PW})
        open(path, "wb").write(urllib.request.urlopen(req, timeout=300).read())
    _paths[sid] = path
    _ids[os.path.basename(file_name)] = sid
    return path


def _known_id(path):
    """The library id of a file already in the library (never uploads)."""
    name = os.path.basename(path)
    if name in _ids:
        return _ids[name]
    try:
        _ids[name] = call("GET", "/api/find", file_name=name)["id"]
        return _ids[name]
    except Exception:
        return None


def _same(a, b, tol=1.5):
    return a and b and len(a) == 4 and len(b) == 4 and all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))


def _corrected(kind, path, body):
    sid = _known_id(path)
    if not sid:
        return body
    try:
        ed = call("GET", f"/api/sheets/{sid}/edits", timeout=20)
    except urllib.error.HTTPError as e:
        if e.code == 404:                       # deleted from the library: forget it
            _ids.pop(os.path.basename(path), None)
        return body
    if not ed["removed"] and not ed["added"]:
        return body
    gone = {r["id"] for r in ed["removed"]}
    boxes = [r["bbox"] for r in ed["removed"] if r.get("bbox")]
    body = dict(body)
    if kind == "/api/sheet":
        for k in ("rooms", "openings", "fixtures", "walls", "elements", "floors"):
            if isinstance(body.get(k), list):
                body[k] = [x for x in body[k] if not (isinstance(x, dict) and x.get("id") in gone)]
    else:
        body["items"] = [x for x in body.get("items") or [] if not any(_same(x.get("bbox"), b) for b in boxes)]
    raster = (body.get("render") or {}).get("type") == "image" or body.get("kind") == "raster" or body.get("units") == "px"
    W = ((body.get("render") or {}).get("width")) or ed["width"]
    if raster and ed["added"]:
        f = (W / ed["width"]) / ed["view_scale"]          # view-image px -> sheet px
        for a in ed["added"]:
            if not a.get("bbox"):
                continue
            bb = [round(v * f, 1) for v in a["bbox"]]
            if kind == "/api/sheet":
                grp = "openings" if a["cls"] in ("door", "window") else "walls" if a["cls"] == "wall" else "fixtures"
                item = dict(id=a["id"], cls=a["cls"], bbox=bb, conf=1.0, found_by=f"person: {a.get('by') or ''}".strip(), exact=False)
                if grp == "walls":
                    item["polygon"] = [[bb[0], bb[1]], [bb[2], bb[1]], [bb[2], bb[3]], [bb[0], bb[3]]]
                body[grp] = list(body.get(grp) or []) + [item]
            else:
                body["items"] = list(body.get("items") or []) + [dict(type=a["cls"], how="person", label=None, conf=1.0, bbox=bb)]
    body["corrections_applied"] = dict(removed=len(gone), added=len(ed["added"]))
    return body


def patched_load_demo(orig=ui_server.load_demo):
    demo = orig()
    local_solve = demo.solve

    def solve(path, question, generic=False, llm=False):
        src = demo.source_of(path) if hasattr(demo, "source_of") else ""
        if any(f"/{s}/" in path for s in LOCAL_SOURCES):
            r = local_solve(path, question, generic, llm=llm)
            r.setdefault("audit", {"question": question, "steps": []})
            r["audit"]["where"] = "ON THIS LAPTOP"
            r["audit"]["steps"].insert(0, dict(stage="Where", actor="this laptop", detail="Finnish scans use the detector output stored on this laptop; it is not on Modal yet"))
            return r
        try:
            r = call("POST", "/api/solve", dict(sheet_id=sheet_id(path), question=question))
            r.setdefault("audit", {})["where"] = "ON MODAL"
            return r
        except Exception as e:  # Modal unreachable: answer here and say so
            r = local_solve(path, question, generic, llm=llm)
            r["audit"] = {"question": question, "where": "ON THIS LAPTOP (MODAL UNREACHABLE)",
                          "steps": [dict(stage="Where", actor="this laptop", detail=f"the library on Modal did not answer: {type(e).__name__}")]}
            return r

    demo.solve = solve

    class H(demo.H):
        def _proxy(self, method):
            """/bob/lib/<path> -> the library on Modal, with the workspace password added here (the page never holds it)."""
            u = urllib.parse.urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            url = URL + u.path[len("/bob/lib"):] + (("?" + u.query) if u.query else "")
            req = urllib.request.Request(url, data=body, method=method, headers={"x-bob-password": PW, "content-type": self.headers.get("content-type") or "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    data, ctype, code, extra = r.read(), r.headers.get("content-type") or "application/json", r.status, r.headers.get("x-changed-regions")
            except urllib.error.HTTPError as e:
                data, ctype, code, extra = e.read(), e.headers.get("content-type") or "application/json", e.code, None
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if extra:
                self.send_header("x-changed-regions", extra)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            p = u.path
            if p.startswith("/bob/lib/"):
                return self._proxy("GET")
            if p == "/bob/local":                   # a library blueprint as a file on this laptop, so the viewer can open it
                qs = urllib.parse.parse_qs(u.query)
                try:
                    return self._send(200, dict(path=_local(int(qs["sid"][0]), (qs.get("file_name") or [""])[0])))
                except Exception as e:
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            if p == "/bob/floors":                  # the other floors of the same building (sibling plan files), top floor first
                path = (urllib.parse.parse_qs(u.query).get("path") or [""])[0]
                return self._send(200, _floors(path))
            if p == "/bob/sid":                     # the library id of an open file (registered the first time), and a revision guess
                path = (urllib.parse.parse_qs(u.query).get("path") or [""])[0]
                try:
                    sid = sheet_id(path)
                    return self._send(200, dict(id=sid, revision_suggestion=_suggest.pop(sid, None)))
                except Exception as e:
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            if p.startswith("/bob/") and os.path.basename(p) in ("onebit.css", "onebit.js", "tools.js", "tools.css", "projects.js"):
                f = os.path.join(REPO, "ui", "bob", os.path.basename(p))
                return self._send(200, open(f, "rb").read(), "text/css; charset=utf-8" if p.endswith(".css") else "text/javascript; charset=utf-8")
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith("/bob/lib/"):
                return self._proxy("POST")
            u = urllib.parse.urlparse(self.path)
            if u.path == "/bob/upload":             # a blueprint into a project: kept here for the viewer, registered in the library
                qs = urllib.parse.parse_qs(u.query)
                project, name = (qs.get("project") or [""])[0], os.path.basename((qs.get("name") or ["file"])[0])
                try:
                    data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                    d = os.path.join(UPLOADS, _slug(project))
                    os.makedirs(d, exist_ok=True)
                    path = os.path.join(d, name)
                    open(path, "wb").write(data)
                    up = call("POST", "/api/sheets", data, "application/octet-stream", project=project, name=os.path.splitext(name)[0], file_name=name)
                    _ids[name] = up["id"]
                    _paths[up["id"]] = path
                    return self._send(200, dict(up, path=path))
                except Exception as e:
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return super().do_POST()

        def _send(self, code, body, ctype="application/json"):
            """The viewer's reading and plan with the library's applied corrections on top (removed items gone, added ones in),
            so the marks, the clean view and the 3D model follow a correction at once."""
            u = urllib.parse.urlparse(self.path)
            if code == 200 and u.path in ("/api/sheet", "/api/plan"):
                try:
                    raw = body if isinstance(body, dict) else json.loads(body.decode() if isinstance(body, bytes) else body)
                    fixed = _corrected(u.path, (urllib.parse.parse_qs(u.query).get("path") or [""])[0], raw)
                    if fixed is not raw:
                        body = fixed if isinstance(body, dict) else json.dumps(fixed, ensure_ascii=False, default=str).encode()
                except Exception as e:  # a correction must never break the viewer
                    print("[bob] corrections not applied:", type(e).__name__, e)
            return super()._send(code, body, ctype)

    demo.H = H
    return demo


ui_server.load_demo = patched_load_demo

if __name__ == "__main__":
    ui_server.main()
