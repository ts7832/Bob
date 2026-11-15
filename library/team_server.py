"""Kokoro's team page, with questions answered on Modal (Oliver's workspace) and kept in the library.

    PORT=8770 python library/team_server.py        (from the repo root, with the project venv)

Runs tools/ui_server.py unchanged, with three additions:
  - /bob/onebit.css and /bob/onebit.js are served (the One-bit design, the QR button and the audit trail);
  - the engine call behind /api/ask goes to the library on Modal (POST /api/solve): the blueprint is registered in the
    library the first time it is asked about, each question is split into its parts, and every answer and part is kept,
    so asking it (or one of its parts) again is answered from the library;
  - Finnish scans (cubicasa5k) are still answered on this laptop: their detector output is not on Modal yet. The audit
    trail says where every answer was made.
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
        sid = call("POST", "/api/sheets", open(path, "rb").read(), "application/octet-stream", project=PROJECT,
                   name=os.path.splitext(name)[0], file_name=name)["id"]
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
        def do_GET(self):
            p = urllib.parse.urlparse(self.path).path
            if p in ("/bob/onebit.css", "/bob/onebit.js"):
                f = os.path.join(REPO, "ui", "bob", os.path.basename(p))
                return self._send(200, open(f, "rb").read(), "text/css; charset=utf-8" if p.endswith(".css") else "text/javascript; charset=utf-8")
            return super().do_GET()

    demo.H = H
    return demo


ui_server.load_demo = patched_load_demo

if __name__ == "__main__":
    ui_server.main()
