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
        return _ids[name]
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
            if p == "/bob/sid":                     # the library id of an open file (registered the first time), and a revision guess
                path = (urllib.parse.parse_qs(u.query).get("path") or [""])[0]
                try:
                    sid = sheet_id(path)
                    return self._send(200, dict(id=sid, revision_suggestion=_suggest.pop(sid, None)))
                except Exception as e:
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            if p.startswith("/bob/") and os.path.basename(p) in ("onebit.css", "onebit.js", "tools.js", "tools.css"):
                f = os.path.join(REPO, "ui", "bob", os.path.basename(p))
                return self._send(200, open(f, "rb").read(), "text/css; charset=utf-8" if p.endswith(".css") else "text/javascript; charset=utf-8")
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith("/bob/lib/"):
                return self._proxy("POST")
            return super().do_POST()

    demo.H = H
    return demo


ui_server.load_demo = patched_load_demo

if __name__ == "__main__":
    ui_server.main()
