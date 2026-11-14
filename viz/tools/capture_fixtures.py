"""Save the team demo's real API responses as fixtures for the view (viz/fixtures/).

Run the demo first:  cd Bob && DRAWQA_LLM=0 PORT=8765 .venv/bin/python demo/server.py
Then:               python3 viz/tools/capture_fixtures.py [--base http://localhost:8765]

Per sample drawing: render.json, plan.json, sheet.json, ask.json (dataset questions + probes), and the page
image for raster/PDF-page drawings. index.json lists them. Read-only against the server; no team files touched.
Absolute paths in the responses (they contain the local user name) are cut to repo-relative ones.
"""
import argparse, json, os, time, urllib.parse, urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")
# Probes beyond the dataset questions: honesty cases and judge-style wording.
PROBES = ["How many doors are there?", "What is the total floor area?", "What is the ceiling height?"]


def scrub(o):
    """Absolute local paths -> relative ones (from junction-dataset-preview/ or the repo root), recursively."""
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    if isinstance(o, str) and o.startswith("/") and os.sep in o[1:]:
        for cut in ("junction-dataset-preview/", "/Bob/"):
            if cut in o:
                return o[o.index(cut):].lstrip("/").replace("Bob/", "", 1)
        return os.path.basename(o)
    return o


def get(base, path, raw=False):
    with urllib.request.urlopen(base + path, timeout=600) as r:
        data = r.read()
    return data if raw else json.loads(data)


def ask(base, path, question):
    req = urllib.request.Request(base + "/api/ask", data=json.dumps(dict(path=path, question=question)).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        res = dict(error=f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")
    return res, round(time.time() - t, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8765")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    index = []
    for s in get(a.base, "/api/samples"):
        path, stem = s["path"], os.path.splitext(os.path.basename(s["path"]))[0]
        d = os.path.join(OUT, stem)
        os.makedirs(d, exist_ok=True)
        q = urllib.parse.quote(path)
        render = get(a.base, f"/api/render?path={q}")
        for name, obj in (("render", render), ("plan", get(a.base, f"/api/plan?path={q}")),
                          ("sheet", get(a.base, f"/api/sheet?path={q}"))):
            json.dump(scrub(obj), open(os.path.join(d, name + ".json"), "w"), ensure_ascii=False)
        if render.get("type") == "image" and render.get("url"):
            ext = os.path.splitext(urllib.parse.urlparse(render["url"]).path)[1].lower()
            fname = "image" + (ext if ext in (".png", ".jpg", ".jpeg") else ".png")
            with open(os.path.join(d, fname), "wb") as f:
                f.write(get(a.base, render["url"], raw=True))
        asks = []
        for item in s["questions"] + [dict(question=p, answer=None, unit=None, probe=True) for p in PROBES]:
            res, dt = ask(a.base, path, item["question"])
            asks.append(dict(question=item["question"], truth=item.get("answer"), truth_unit=item.get("unit"),
                             probe=bool(item.get("probe")), seconds=dt, response=res))
            print(f"  {stem[:28]:28} {dt:5.1f}s  {str(res.get('answer'))[:14]:14} "
                  f"{(res.get('honesty') or {}).get('tier', res.get('error', ''))[:30]:30} {item['question'][:60]}")
        json.dump(scrub(asks), open(os.path.join(d, "ask.json"), "w"), ensure_ascii=False, indent=1)
        index.append(dict(stem=stem, path=path, title=s["title"], source=s["source"], render_type=render.get("type")))
    json.dump(index, open(os.path.join(OUT, "index.json"), "w"), ensure_ascii=False, indent=1)
    print(f"saved {len(index)} drawings to {os.path.abspath(OUT)}")


if __name__ == "__main__":
    main()
