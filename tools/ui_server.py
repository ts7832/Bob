"""DrawQA UI server: python tools/ui_server.py [--port 8770] [--viz path/to/drawview.js]  ->  http://localhost:8770

Serves our page (ui/index.html) on top of the demo server's API without editing demo/: it loads demo/server.py as a
module and subclasses its request handler, so /api/*, /page, /file and /data/ behave exactly as in the demo (one
process, no proxy hop). Added routes:

  /                  ui/index.html (the judge-facing page)
  /classic           demo/index.html (the original demo page, unchanged)
  /viz/drawview.js   oliraff's drawing view bundle (viz/dist/drawview.js once PR #5 is merged, or --viz / DRAWQA_VIZ)
  /api/gold          the checked gold questions (gold/gold.jsonl, status verified or exact), grouped by drawing,
                     for the page's rehearsed questions and its "checked answer" comparison

The demo's own "/" moves to /classic; nothing else changes.
"""
import argparse, collections, importlib.util, json, os, sys
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
UI = os.path.join(REPO, "ui", "index.html")
GOLD = os.path.join(REPO, "gold", "gold.jsonl")


def load_demo():
    spec = importlib.util.spec_from_file_location("demo_server", os.path.join(REPO, "demo", "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # module level only loads questions and samples; the server starts under __main__
    return mod


def gold_drawings(data_root):
    """Checked gold lines grouped by drawing. A line marked generic is opened as an unknown file, as a judge's file is."""
    groups = collections.OrderedDict()
    with open(GOLD, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            q = json.loads(line)
            if q["status"] not in ("verified", "exact"):
                continue
            key = (q["path"], int(q.get("page") or 1))
            extra = q["path"].startswith("extra/")  # a sheet outside the dataset, kept in gold/extra/ (git-ignored)
            abs_path = os.path.join(REPO, "gold", "extra", q["path"][6:]) if extra else os.path.join(data_root, q["path"])
            if extra and not os.path.isfile(abs_path):
                continue  # this machine does not have it; tools/gold_eval.py reports it as not scored
            g = groups.setdefault(key, dict(path=q["path"], page=key[1], generic=bool(q.get("generic")) or extra,
                                            abs_path=abs_path, topic=q.get("topic"), questions=[]))
            g["generic"] = g["generic"] or bool(q.get("generic"))
            g["questions"].append(dict(id=q["id"], question=q["question"], answer=q["answer"], unit=q["unit"], type=q["type"],
                                       expected_tier=q.get("expected_tier"), status=q["status"], evidence_note=q.get("evidence_note")))
    return list(groups.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8770)))
    ap.add_argument("--viz", default=os.environ.get("DRAWQA_VIZ", os.path.join(REPO, "viz", "dist", "drawview.js")))
    a = ap.parse_args()
    if not os.path.isfile(UI):
        raise SystemExit(f"missing {UI}")
    viz_ok = os.path.isfile(a.viz)
    if not viz_ok:  # the page still runs and says why the drawing view is missing
        print(f"warning: drawing view bundle not found at {a.viz}; merge PR #5 (viz/) or pass --viz <path to drawview.js>")
    demo = load_demo()

    class H(demo.H):
        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, open(UI, "rb").read(), "text/html; charset=utf-8")
                if u.path == "/classic":
                    return self._send(200, open(os.path.join(REPO, "demo", "index.html"), "rb").read(), "text/html; charset=utf-8")
                if u.path == "/viz/drawview.js":
                    if not viz_ok:
                        return self._send(404, f"// drawview.js not found at {a.viz}: merge PR #5 or start with --viz".encode(),
                                          "text/javascript; charset=utf-8")
                    return self._send(200, open(a.viz, "rb").read(), "text/javascript; charset=utf-8")
                if u.path == "/api/gold":
                    return self._send(200, gold_drawings(demo.DATA))
            except Exception as e:  # same contract as the demo: errors reach the page as JSON
                import traceback
                traceback.print_exc()
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return super().do_GET()

    print(f"DrawQA UI on http://localhost:{a.port}  (classic demo page: /classic; drawing view: {'ok' if viz_ok else 'MISSING'})")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
