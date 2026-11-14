"""Picture-first reading of any drawing: one cached LAYER per file and page, the single source for answers.

    ingest(path, page=0, discover=True) -> layer dict      (cached in data/cache/layers/<hash>_p<page>/layer.json)

What people see decides what exists, so every input becomes a page picture first:
  image (PNG/JPG/TIFF)  -> itself
  PDF                   -> the page rendered (the engine also keeps the PDF's own text layer and linework)
  DXF / DWG             -> drawn onto a vector PDF page (drawqa/cad.py: blocks expanded, hidden layers not drawn)
  IFC                   -> no picture: ask.py answers from the building model itself (drawqa/ifc.py)
Then:
  1. our trained models on that page (drawqa/convert.py): doors, windows, fixtures, walls, rooms, text, scale;
  2. one Claude agent (Opus, DRAWQA_DISCOVER_EFFORT, default medium, headless Claude Code, brief tools/discover/brief_discover_model.md) finds
     every other kind of thing drawn and checks the models' doubtful items; skipped with a note if it is not
     available or runs out of time, so the layer always exists;
  3. every item gets an id ("door#3") so an answer can point at its evidence.
Layer: {source, page, picture, width, height, m_per_px, scale_source, classes:[{name, source, geometry, count,
items:[{id, bbox|polygon|polyline|point, ...}]}], texts:[{text, bbox}], notes:[...], discovery:{...}}
"""
import hashlib, json, os, shutil, subprocess, time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CACHE = os.path.join(ROOT, "data", "cache", "layers")
TOOLS = os.path.join(ROOT, "tools", "discover")
BRIEF = os.path.join(TOOLS, "brief_discover_model.md")
DISCOVER_TIMEOUT = int(os.environ.get("DRAWQA_DISCOVER_TIMEOUT", "600"))  # seconds for the Claude step
DISCOVER_EFFORT = os.environ.get("DRAWQA_DISCOVER_EFFORT", "medium")  # user's choice (15 Nov): medium for the reading step


LAYER_VERSION = 2  # bump when the reading changes (v2: no wall/room model on big sheets, Claude outlines rooms there)


def _key(path, page):
    h = hashlib.sha1(open(path, "rb").read()).hexdigest()[:16]
    return f"{h}_p{page}_v{LAYER_VERSION}"


def page_source(path, page=0):
    """The file the engine reads for this page (a PDF for CAD files) and the page picture (PNG path), or (None, None)
    for a building model. Raises cad.CadError with the reason when a CAD file cannot be drawn."""
    from .convert import sniff
    kind = sniff(path)
    out = os.path.join(CACHE, _key(path, page))
    os.makedirs(out, exist_ok=True)
    if kind == "image":
        return path, path, kind
    if kind in ("dxf", "dwg"):
        from . import cad
        path = cad.to_pdf(path)["pdf"]
        kind = "pdf"
    if kind == "pdf":
        import cv2, pymupdf
        from . import pdfsheet
        png = os.path.join(out, "page.png")
        if not os.path.exists(png):
            cv2.imwrite(png, pdfsheet.render(pymupdf.open(path)[page]))
        return path, png, kind
    return None, None, kind  # ifc, svg, unknown: no picture route here


def _start_layer(sheet):
    """The engine's sheet -> layer classes (one per detector class, rooms, walls), each item with the model that made it."""
    from shapely.geometry import Polygon
    r1 = lambda v: [round(float(x), 1) for x in v]
    classes = {}

    def add(name, item, source, geometry):
        c = classes.setdefault(name, dict(name=name, source=source, geometry=geometry, items=[]))
        c["items"].append(item)

    for group in ("openings", "fixtures"):
        for o in sheet.get(group, []):
            add(o["cls"], dict(bbox=r1(o["bbox"]), score=round(float(o.get("conf") or 0), 3)), "trained symbol detector", "box")
    texts = [t for t in sheet.get("texts", []) if t.get("bbox") and t.get("text")]
    for r in sheet.get("rooms", []):
        poly = r.get("polygon")
        if not poly or len(poly) < 3:
            continue
        g = Polygon(poly).buffer(0)
        if r.get("cls") in (None, "unlabelled") and (r.get("outdoor") or (len(poly) > 30 and g.area / (g.convex_hull.area or 1) < 0.85)):
            continue  # an unnamed outline that wanders (outdoors, hatching): not a room
        gs = g.simplify(1.5, preserve_topology=True)
        if gs.geom_type == "Polygon" and len(gs.exterior.coords) >= 4:
            poly = [list(p) for p in list(gs.exterior.coords)[:-1]]
        item = dict(polygon=[r1(p) for p in poly], type=r.get("cls"), label=r.get("name") or r.get("label"))
        if r.get("area_m2"):
            item["area_m2"] = r["area_m2"]
        add("room", item, "wall and room-outline model", "polygon")
    mpp = (sheet.get("scale") or {}).get("m_per_px")
    for w in sheet.get("walls", []):
        if not w.get("axis"):
            continue
        it = dict(polyline=[r1(w["axis"][:2]), r1(w["axis"][2:])], thickness_px=round(float(w.get("thickness_px", 0)), 1))
        if w.get("polygon") and len(w["polygon"]) >= 3:
            it["polygon"] = [r1(p) for p in w["polygon"]]
        add("wall", it, "wall and room-outline model", "polyline")
    for c in classes.values():
        c["count"] = len(c["items"])
    scale = sheet.get("scale") or {}
    return dict(m_per_px=mpp, scale_source=scale.get("source"), classes=sorted(classes.values(), key=lambda c: c["name"]),
                texts=[dict(text=t["text"], bbox=r1(t["bbox"])) for t in texts])


def _overlay(png, layer, out):
    from PIL import Image, ImageDraw
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(png).convert("RGB")
    s = min(1.0, 2400 / im.width)
    im = im.resize((int(im.width * s), int(im.height * s)))
    d = ImageDraw.Draw(im)
    pal = [(220, 40, 40), (30, 110, 220), (20, 150, 70), (200, 120, 0), (140, 60, 200), (0, 150, 160), (180, 30, 120)]
    for k, c in enumerate(layer["classes"]):
        for it in c["items"]:
            if "bbox" in it:
                d.rectangle([v * s for v in it["bbox"]], outline=pal[k % len(pal)], width=2)
            elif it.get("polygon"):
                d.polygon([(x * s, y * s) for x, y in it["polygon"]], outline=pal[k % len(pal)])
    im.save(out)


def _discover(png, w, h, wd, start):
    """Opus (low effort) through headless Claude Code, following the discovery brief. Returns (classes or None, info)."""
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(exe):
        return None, dict(status="skipped", reason="Claude Code is not installed here")
    json.dump(dict(start, sheet="start layer from the trained models"), open(os.path.join(wd, "start_layer.json"), "w"), indent=1)
    json.dump(start["texts"], open(os.path.join(wd, "ocr_text.json"), "w"))
    _overlay(png, start, os.path.join(wd, "start_overlay.png"))
    prompt = (f"Follow the brief {BRIEF} exactly.\nImage: {png} ({w} x {h} px).\n"
              f"Work folder: {wd} (start_layer.json and start_overlay.png from our trained models are already in it).")
    t0 = time.time()
    try:
        r = subprocess.run([exe, "-p", prompt, "--model", "opus", "--effort", DISCOVER_EFFORT, "--output-format", "json",
                            "--allowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep",
                            "--add-dir", wd, "--add-dir", TOOLS, "--add-dir", os.path.dirname(os.path.abspath(png))],
                           cwd=wd, capture_output=True, text=True, timeout=DISCOVER_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, dict(status="timeout", seconds=round(time.time() - t0), reason=f"the Claude step took over {DISCOVER_TIMEOUT} s")
    info = dict(status="done", seconds=round(time.time() - t0), model=f"Opus, {DISCOVER_EFFORT} effort")
    try:
        meta = json.loads(r.stdout or "{}")
        info["cost_usd"] = meta.get("total_cost_usd")
        info["report"] = (meta.get("result") or "")[-3000:]
    except Exception:
        pass
    f = os.path.join(wd, "discovered.json")
    if not os.path.exists(f):
        return None, dict(info, status="failed", reason="the Claude step wrote no result" + (f": {r.stderr[-300:]}" if r.stderr else ""))
    try:
        return json.load(open(f)).get("classes", []), info
    except Exception as e:
        return None, dict(info, status="failed", reason=f"unreadable result: {e}")


def ingest(path, page=0, discover=True, refresh=False, progress=None):
    """progress(stage, detail) is called with 'picture', 'models', 'claude', 'done' (for a status display)."""
    from . import convert
    say = progress or (lambda *a: None)
    key = _key(path, page)
    out = os.path.join(CACHE, key)
    lf = os.path.join(out, "layer.json")
    if os.path.exists(lf) and not refresh:
        layer = json.load(open(lf))
        if layer.get("discovery", {}).get("status") == "done" or not discover:
            return layer
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    notes = []
    say("picture", "drawing the page as a picture")
    try:
        src, png, kind = page_source(path, page)
    except Exception as e:  # e.g. a DWG without the converter
        return dict(source=path, page=page, picture=None, classes=[], texts=[], notes=[f"could not be drawn as a picture: {e}"], kind="unreadable")
    if png is None:
        return dict(source=path, page=page, picture=None, classes=[], texts=[], kind=kind,
                    notes=[f"{kind} file: no picture route; answered from the file itself where possible"])
    say("models", "our trained models: doors, windows, fixtures, walls, rooms, text, scale")
    sheet = convert.convert(src, page) if kind == "pdf" else convert.convert(src)
    start = _start_layer(sheet)
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    w, h = Image.open(png).size
    for k in ("legend_note", "detector_note", "outline_note"):
        if sheet.get(k):
            notes.append(sheet[k])
    layer = dict(source=os.path.abspath(path), page=page, kind=sheet.get("kind"), picture=os.path.abspath(png), width=w, height=h,
                 m_per_px=start["m_per_px"], scale_source=start["scale_source"], classes=start["classes"], texts=start["texts"],
                 notes=notes, models_seconds=round(time.time() - t0, 1))
    if discover:
        wd = os.path.join(out, "discover", time.strftime("%Y%m%d-%H%M%S"))  # a fresh folder per run: no stale files
        os.makedirs(wd, exist_ok=True)
        layer["start_layer_file"] = os.path.join(wd, "start_layer.json")
        say("claude", f"Claude (Opus, {DISCOVER_EFFORT} effort) is reading the whole drawing")
        classes, info = _discover(layer["picture"], w, h, wd, start)
        layer["discovery"] = info
        if classes:
            layer["classes"] = classes
        else:
            layer["notes"].append("only our trained models' reading is available: " + str(info.get("reason", "")))
    else:
        layer["discovery"] = dict(status="not run")
    for c in layer["classes"]:
        c["count"] = len(c.get("items") or [])
        for i, it in enumerate(c.get("items") or [], 1):
            it["id"] = f"{c['name']}#{i}"
    json.dump(layer, open(lf, "w"), indent=1)
    say("done", "ready")
    return layer
