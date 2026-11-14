"""DrawQA UI server: python tools/ui_server.py [--port 8770] [--viz path/to/drawview.js]  ->  http://localhost:8770

Serves our page (ui/index.html) on top of the demo server's API without editing demo/: it loads demo/server.py as a
module and subclasses its request handler, so /api/*, /page, /file and /data/ behave exactly as in the demo (one
process, no proxy hop). Added routes:

  /                  ui/index.html (the judge-facing page)
  /classic           demo/index.html (the original demo page, unchanged)
  /viz/drawview.js   oliraff's drawing view bundle (viz/dist/drawview.js once PR #5 is merged, or --viz / DRAWQA_VIZ)
  /api/gold          the checked gold questions (gold/gold.jsonl, status verified or exact), grouped by drawing,
                     for the page's rehearsed questions and its "checked answer" comparison
  /api/pages?path=   the sheets of a multi-page PDF (page, sheet number, title from the title block)
  /api/sheetpage?path=&page=N   that page cut out as its own one-page PDF and opened as a drawing (drawqa reads page 1)
  POST /api/feedback a correction from the page ("Wrong answer?"), appended to gold/feedback.jsonl for the team to check
  /api/cost?path=    what one drawing has cost so far (tools/ui_cost.py meters every /api/* request: model calls,
                     tokens, GPU time, dollars); /api/ask answers also carry their own `cost`, with the drawing's total

The demo's own "/" moves to /classic; nothing else changes.
"""
import argparse, collections, hashlib, importlib.util, io, json, os, re, sys, threading, time
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
UI = os.path.join(REPO, "ui", "index.html")
GOLD = os.path.join(REPO, "gold", "gold.jsonl")
FEEDBACK = os.path.join(REPO, "gold", "feedback.jsonl")  # corrections from the page's "Wrong answer?" form
PAGES = os.path.join(REPO, "data", "uploads", "pages")  # single pages cut from multi-page sets (git-ignored)


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
                                       expected_tier=q.get("expected_tier"), status=q["status"], evidence_note=q.get("evidence_note"),
                                       tolerance=q.get("tolerance")))
    return list(groups.values())


SHEET_NO = re.compile(r"^[A-Z]{1,3}[-.]?\d{1,4}(\.\d+)?[A-Z]?$")


def pdf_pages(path):
    """Each page of a PDF with its sheet number and title, read from the page's largest text (the title block).
    Text that repeats on most pages (project name, address, firm) is not a title."""
    import pymupdf
    with pymupdf.open(path) as doc:
        per_page = []
        for pg in doc:
            spans = [(s["size"], s["text"].strip()) for b in pg.get_text("dict")["blocks"] if b["type"] == 0
                     for l in b["lines"] for s in l["spans"] if s["text"].strip()]
            per_page.append(sorted(spans, key=lambda t: -t[0])[:12])
        n = len(per_page)
        common = collections.Counter(t for spans in per_page for t in {t for _, t in spans})
        out = []
        for i, spans in enumerate(per_page):
            texts = [t for _, t in spans]
            number = next((t for t in texts if SHEET_NO.match(t)), None)
            title = next((t for t in texts if t != number and (n < 3 or common[t] <= n / 2) and len(t) > 3
                          and sum(c.isdigit() for c in t) < len(t) / 3), None)
            out.append(dict(page=i + 1, number=number, title=title))
        return out


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
    sys.path.insert(0, HERE)
    import ui_cost
    for line in ui_cost.instrument():  # before any model client exists; patches classes, so import order does not matter
        print(f"cost meter: {line}")
    demo = load_demo()

    per_drawing = collections.defaultdict(lambda: dict(usd=0.0, unpriced_calls=0, model_calls=0, requests=0))
    lock = threading.Lock()

    class H(demo.H):
        # Every /api/* request is metered; its model calls are added to the drawing it is about. /api/ask answers
        # also carry their own cost and the drawing's running total, so the page can say what reading this drawing
        # and answering its questions has cost so far (ingestion included: the first render/plan converts the file).
        def _meter(self, drawing, inner):
            self._cost_path, self._cost_ask = drawing, urlparse(self.path).path == "/api/ask"
            ui_cost.start()
            try:
                return inner()
            finally:
                if ui_cost.active():  # the demo answered without our _send (an exception path): close the meter
                    self._account(ui_cost.summary(), None)

        def _account(self, s, response_body):
            key = self._cost_path or (response_body.get("path") if isinstance(response_body, dict) else None)
            if not key:
                return None
            with lock:
                t = per_drawing[key]
                t["requests"] += 1
                t["model_calls"] += s["model_calls"]
                t["usd"] += s["usd_known"]
                t["unpriced_calls"] += len(s["incomplete"])
                return dict(t)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/api/cost":  # running cost of one drawing
                key = (parse_qs(u.query).get("path") or [""])[0]
                with lock:
                    return self._send(200, dict(per_drawing.get(key) or dict(usd=0.0, unpriced_calls=0, model_calls=0, requests=0)))
            if u.path.startswith("/api/"):
                return self._meter((parse_qs(u.query).get("path") or [None])[0], lambda: self._get(u))
            return self._get(u)

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/api/feedback":  # "Wrong answer?" on the page: kept for the team to check and add to the gold set
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                    entry = {k: body.get(k) for k in ("path", "file", "question", "answer", "unit", "tier", "correction", "note")}
                    if not entry["question"] or not entry["correction"]:
                        return self._send(400, {"error": "a question and the right answer are needed"})
                    entry["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    with lock:
                        with open(FEEDBACK, "a", encoding="utf-8") as f:
                            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                        with open(FEEDBACK, encoding="utf-8") as f:
                            saved = sum(1 for line in f if line.strip())
                    return self._send(200, {"ok": True, "saved": saved, "file": os.path.relpath(FEEDBACK, REPO)})
                except Exception as e:
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            if not u.path.startswith("/api/"):
                return super().do_POST()
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            self.rfile = io.BytesIO(raw)  # the demo handler reads the body again
            drawing = None
            if u.path in ("/api/ask", "/api/read"):
                try:
                    drawing = json.loads(raw).get("path")
                except ValueError:
                    pass
            return self._meter(drawing, super().do_POST)

        def _send(self, code, body, ctype="application/json"):
            if ui_cost.active() and isinstance(body, (dict, list)):
                s = ui_cost.summary()
                total = self._account(s, body)
                if getattr(self, "_cost_ask", False) and isinstance(body, dict):
                    body = dict(body, cost=dict(s, drawing=total))
            return super()._send(code, body, ctype)

        def _get(self, u):
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
                if u.path == "/api/pages":  # the sheets of a multi-page PDF, by number and title
                    f = demo.resolve(parse_qs(u.query)["path"][0])
                    if not f.lower().endswith(".pdf"):
                        return self._send(200, dict(pages=[]))
                    return self._send(200, dict(pages=pdf_pages(f)))
                if u.path == "/api/sheetpage":  # one page of a set as its own drawing: drawqa reads the first page of a PDF
                    qs = parse_qs(u.query)
                    f, page = demo.resolve(qs["path"][0]), int(qs["page"][0])
                    import pymupdf
                    os.makedirs(PAGES, exist_ok=True)
                    tag = hashlib.sha1(os.path.abspath(f).encode()).hexdigest()[:10]
                    out = os.path.join(PAGES, f"{tag}_p{page}_{re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(f))[-60:]}")
                    if not os.path.exists(out):
                        with pymupdf.open(f) as src:
                            if not 1 <= page <= len(src):
                                return self._send(400, {"error": f"page {page} is not in this PDF ({len(src)} pages)"})
                            one = pymupdf.open()
                            one.insert_pdf(src, from_page=page - 1, to_page=page - 1)
                            one.save(out)
                            one.close()
                    return self._send(200, demo.open_file(out))
            except Exception as e:  # same contract as the demo: errors reach the page as JSON
                import traceback
                traceback.print_exc()
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return super().do_GET()

    print(f"DrawQA UI on http://localhost:{a.port}  (classic demo page: /classic; drawing view: {'ok' if viz_ok else 'MISSING'})")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
