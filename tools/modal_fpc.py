"""FloorPlanCAD (CAD-drawn floor plans, every SVG primitive labelled) -> YOLO tiles for our detector. Prepare only.

Source: https://floorplancad.github.io (Fan et al., "FloorPlanCAD: A Large-Scale CAD Drawing Dataset for Panoptic
Symbol Spotting", ICCV 2021). Annotations: CC BY-NC 4.0; the authors do not own the drawings' copyright.

Writes ONLY under /data/floorplancad/ (archives, unpacked SVGs, logs) and /data/yolo_fpc/ (YOLO format, our class
names in our order). Long stages are spawned from a DETACHED run (a plain `modal run` stops its app, and with it every
spawned call, when the local entrypoint returns); progress and results land on the volume.

  .venv/bin/modal run --detach tools/modal_fpc.py --stage download   # gdown the 3 Google Drive archives, unpack SVGs
  .venv/bin/modal run --detach tools/modal_fpc.py --stage render     # all blocks -> /data/yolo_fpc (+ stats.json)
  .venv/bin/modal run tools/modal_fpc.py --stage status              # download / render logs + stats.json
  .venv/bin/modal run tools/modal_fpc.py --stage overlays --n 6      # label boxes drawn on built images (DRAWQA_SPLIT, DRAWQA_OUT)
  .venv/bin/modal run tools/modal_fpc.py --stage probe | gallery | semviz | peek   # format / class-id checks
"""
import glob, json, os, pathlib, time

import modal

APP = modal.App("drawqa-fpc")
VOL = modal.Volume.from_name("drawqa-data", create_if_missing=True)
ROOT = pathlib.Path(__file__).resolve().parent.parent
D = "/data"
F = f"{D}/floorplancad"
Y = f"{D}/yolo_fpc"

DRIVE = {  # floorplancad.github.io download links (Google Drive file ids)
    "train1": "1HcyKt6qWeXog-tRfvEjdO3O3TN91PXGL",
    "train2": "1kSS7OB_EEu7VJzb0W8DK9_nu1EvshioV",
    "test": "1jxpYgxnLUbXEzMOsjaMPQFSuvmvHimiZ",
}

dl_image = modal.Image.debian_slim(python_version="3.11").apt_install("xz-utils", "unzip", "curl", "file").pip_install("gdown==5.2.0")


def _log(name, data):
    os.makedirs(F, exist_ok=True)
    json.dump(data, open(f"{F}/{name}.json", "w"), indent=1, default=str)
    VOL.commit()


# ---- SVG -> geometry -----------------------------------------------------------------------------------------------
# FloorPlanCAD SVG: viewBox 0 0 100 100 (one block of a larger CAD drawing), <path>/<circle>/<ellipse> primitives with
# stroke-width 0.1, layer colours, and attributes semantic-id (1..35; absent = background) and instance-id (-1 = stuff).
# <text> elements carry a transform. Geometry is turned into polylines in SVG units (arcs sampled), which gives both the
# drawing and exact instance boxes.

def _matrix(t):
    """SVG transform attribute -> [a b c d e f] (x' = a x + c y + e, y' = b x + d y + f)."""
    import math, re
    mul = lambda p, q: [p[0] * q[0] + p[2] * q[1], p[1] * q[0] + p[3] * q[1], p[0] * q[2] + p[2] * q[3], p[1] * q[2] + p[3] * q[3],
                        p[0] * q[4] + p[2] * q[5] + p[4], p[1] * q[4] + p[3] * q[5] + p[5]]
    m = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", t or ""):
        v = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "matrix" and len(v) == 6:
            n = v
        elif name == "translate":
            n = [1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0]
        elif name == "scale":
            n = [v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0]
        elif name == "rotate":
            a = math.radians(v[0])
            n = [math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0]
            if len(v) == 3:
                n = mul(mul([1, 0, 0, 1, v[1], v[2]], n), [1, 0, 0, 1, -v[1], -v[2]])
        else:
            continue
        m = mul(m, n)
    return m


def _parse(path):
    """-> (viewBox, prims, texts); prim = (polylines in SVG units, semantic id, instance id, stroke rgb)."""
    import math, re
    import xml.etree.ElementTree as ET
    from svgpathtools import parse_path, Line, Arc
    root = ET.parse(path).getroot()
    vb = tuple(float(v) for v in root.attrib["viewBox"].replace(",", " ").split())
    prims, texts = [], []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        a = el.attrib
        if tag == "text":
            s = "".join(el.itertext()).strip()
            if s:
                fs = re.findall(r"[\d.]+", a.get("font-size", "1"))
                texts.append((s, float(a.get("x", 0) or 0), float(a.get("y", 0) or 0), float(fs[0]) if fs else 1.0, _matrix(a.get("transform"))))
            continue
        if tag not in ("path", "circle", "ellipse", "line"):
            continue
        try:
            sem, inst = int(a.get("semantic-id", 0)), int(a.get("instance-id", -1))
            polys = []
            if tag == "path":
                cur = None
                for seg in parse_path(a["d"]):
                    if isinstance(seg, Line):
                        pts = [seg.start, seg.end]
                    else:
                        n = 24 if isinstance(seg, Arc) else 12
                        pts = [seg.point(i / n) for i in range(n + 1)]
                    if cur is not None and abs(cur[-1] - pts[0]) < 1e-6:
                        cur.extend(pts[1:])
                    else:
                        cur = list(pts)
                        polys.append(cur)
                polys = [[(p.real, p.imag) for p in poly] for poly in polys]
            elif tag == "line":
                polys = [[(float(a["x1"]), float(a["y1"])), (float(a["x2"]), float(a["y2"]))]]
            else:
                cx, cy = float(a["cx"]), float(a["cy"])
                rx, ry = (float(a["r"]), float(a["r"])) if tag == "circle" else (float(a["rx"]), float(a["ry"]))
                m = _matrix(a.get("transform"))
                pts = []
                for i in range(49):
                    t = 2 * math.pi * i / 48
                    x, y = cx + rx * math.cos(t), cy + ry * math.sin(t)
                    pts.append((m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]))
                polys = [pts]
        except Exception:
            continue
        col = tuple(int(v) for v in re.findall(r"\d+", a.get("stroke", ""))[:3])
        prims.append((polys, sem, inst, col if len(col) == 3 else (0, 0, 0)))
    return vb, prims, texts


def _instances(prims):
    """{(semantic id, instance id): [x0, y0, x1, y1]} in SVG units, for thing primitives that carry an instance id."""
    out = {}
    for polys, sem, inst, _ in prims:
        if sem <= 0 or inst < 0:
            continue
        xs = [x for p in polys for x, _ in p]
        ys = [y for p in polys for _, y in p]
        if not xs:
            continue
        b = out.setdefault((sem, inst), [1e18, 1e18, -1e18, -1e18])
        b[0], b[1], b[2], b[3] = min(b[0], min(xs)), min(b[1], min(ys)), max(b[2], max(xs)), max(b[3], max(ys))
    return out


@APP.function(image=dl_image, volumes={D: VOL}, timeout=3 * 3600, cpu=4, memory=8192)
def download():
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    VOL.reload()
    os.makedirs(f"{F}/archives", exist_ok=True)
    log = dict(started=time.ctime(), files={})

    def get(item):
        name, fid = item
        have = glob.glob(f"{F}/archives/{name}.*")
        if have and os.path.getsize(have[0]) > 1e6:
            return name, dict(path=have[0], size=os.path.getsize(have[0]), cached=True)
        tmp = f"/tmp/{name}.bin"
        r = subprocess.run(["gdown", "--fuzzy", "-O", tmp, f"https://drive.google.com/uc?id={fid}"], capture_output=True, text=True)
        if not os.path.exists(tmp) or os.path.getsize(tmp) < 1e6:  # gdown refused (quota page): try the direct endpoint
            subprocess.run(["curl", "-L", "-s", "-o", tmp, f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"])
        if not os.path.exists(tmp):
            return name, dict(error=r.stderr[-800:])
        kind = subprocess.run(["file", "-b", tmp], capture_output=True, text=True).stdout.strip()
        ext = ".tar.xz" if "XZ" in kind else ".zip" if "Zip" in kind else ".tar.gz" if "gzip" in kind else ".tar" if "tar" in kind else ".bin"
        dst = f"{F}/archives/{name}{ext}"
        subprocess.run(["cp", tmp, dst], check=True)
        return name, dict(path=dst, size=os.path.getsize(dst), kind=kind, gdown_tail=r.stderr[-300:])

    with ThreadPoolExecutor(3) as ex:
        for name, info in ex.map(get, DRIVE.items()):
            log["files"][name] = info
            _log("download", log)
    for name, info in log["files"].items():
        p = info.get("path")
        if not p:
            continue
        out = f"{F}/raw/{name}"
        if os.path.exists(f"{out}/.done"):
            continue
        os.makedirs(out, exist_ok=True)
        local = f"/tmp/x_{name}"
        os.makedirs(local, exist_ok=True)
        if p.endswith(".zip"):
            cmd = ["unzip", "-q", "-o", p, "-d", local]
        else:
            cmd = ["tar", "-xf", p, "-C", local]
        r = subprocess.run(cmd, capture_output=True, text=True)
        info["unpack_err"] = r.stderr[-500:]
        # only the SVGs go to the volume (the PNGs / coco_vis are not needed: we render ourselves)
        svgs = [os.path.join(dp, f) for dp, _, fs in os.walk(local) for f in fs if f.endswith(".svg")]
        info["svgs"] = len(svgs)
        info["all_files"] = sum(len(fs) for _, _, fs in os.walk(local))
        info["sample_paths"] = sorted(svgs)[:5]
        with ThreadPoolExecutor(32) as ex:
            list(ex.map(lambda s: subprocess.run(["cp", s, f"{out}/{os.path.basename(s)}"]), svgs))
        open(f"{out}/.done", "w").write(str(len(svgs)))
        _log("download", log)
    log["finished"] = time.ctime()
    _log("download", log)
    return log


@APP.function(image=dl_image, volumes={D: VOL}, timeout=600)
def peek(n_chars: int = 3000):
    VOL.reload()
    out = {}
    for name in DRIVE:
        fs = sorted(glob.glob(f"{F}/raw/{name}/*.svg"))
        out[name] = dict(svgs=len(fs), first=[os.path.basename(f) for f in fs[:3]])
        if fs:
            out[name]["head"] = open(fs[0]).read()[:n_chars]
            out[name]["bytes_first"] = os.path.getsize(fs[0])
    return out


@APP.function(image=modal.Image.debian_slim(python_version="3.11").pip_install("svgpathtools==1.6.1", "numpy<2"), volumes={D: VOL}, timeout=1200, cpu=4)
def probe(n: int = 300):
    """Format facts over n SVGs: tags, attributes, stroke widths, viewBoxes, instance sizes per semantic id (SVG units)."""
    import collections, re, statistics
    import xml.etree.ElementTree as ET
    from svgpathtools import parse_path
    VOL.reload()
    fs = sorted(glob.glob(f"{F}/raw/*/*.svg"))
    fs = fs[:: max(1, len(fs) // n)][:n]
    tags, attrs, widths, vbs, colors = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    sizes, per_file = collections.defaultdict(list), collections.Counter()
    door_med = []
    for f in fs:
        root = ET.parse(f).getroot()
        vbs[root.attrib.get("viewBox")] += 1
        groups = collections.defaultdict(lambda: [1e9, 1e9, -1e9, -1e9])
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            tags[tag] += 1
            for a in el.attrib:
                attrs[f"{tag}.{a}"] += 1
            if tag not in ("path", "circle", "ellipse", "line"):
                continue
            widths[el.attrib.get("stroke-width")] += 1
            colors[el.attrib.get("stroke")] += 1
            sem = int(el.attrib.get("semantic-id", 0))
            if sem == 0:
                continue
            inst = int(el.attrib.get("instance-id", -1))
            try:
                if tag == "path":
                    x0, x1, y0, y1 = parse_path(el.attrib["d"]).bbox()
                elif tag == "circle":
                    cx, cy, r = (float(el.attrib[k]) for k in ("cx", "cy", "r"))
                    x0, x1, y0, y1 = cx - r, cx + r, cy - r, cy + r
                elif tag == "ellipse":
                    cx, cy, rx, ry = (float(el.attrib[k]) for k in ("cx", "cy", "rx", "ry"))
                    x0, x1, y0, y1 = cx - rx, cx + rx, cy - ry, cy + ry
                else:
                    continue
            except Exception:
                tags["parse_error"] += 1
                continue
            g = groups[(sem, inst if inst >= 0 else -id(el))]
            g[0], g[1], g[2], g[3] = min(g[0], x0), min(g[1], y0), max(g[2], x1), max(g[3], y1)
        ds = []
        for (sem, inst), b in groups.items():
            if inst < 0 and sem < 31:
                tags[f"thing_without_instance_{sem}"] += 1
            if sem >= 31:
                continue
            w, h = b[2] - b[0], b[3] - b[1]
            sizes[sem].append((max(w, h), min(w, h)))
            per_file[sem] += 1
            if sem == 1:
                ds.append(max(w, h))
        if ds:
            door_med.append(statistics.median(ds))
    q = lambda a, p: round(sorted(a)[int(p * (len(a) - 1))], 3) if a else None
    return dict(files=len(fs), total=len(glob.glob(f"{F}/raw/*/*.svg")), viewboxes=vbs.most_common(8), tags=dict(tags), attrs=attrs.most_common(40),
                stroke_widths=widths.most_common(10), colors=colors.most_common(12),
                instances={s: dict(n=len(v), max_side=[q([a for a, _ in v], p) for p in (0.1, 0.5, 0.9)], min_side=[q([b for _, b in v], p) for p in (0.1, 0.5, 0.9)]) for s, v in sorted(sizes.items())},
                single_door_median_per_file=[q(door_med, p) for p in (0.05, 0.25, 0.5, 0.75, 0.95)], files_with_single_door=len(door_med))


render_image = (modal.Image.debian_slim(python_version="3.11")
                .apt_install("libcairo2", "fonts-dejavu-core")
                .pip_install("cairocffi==1.7.1", "svgpathtools==1.6.1", "numpy<2", "opencv-python-headless==4.10.0.84"))

SEM_COLORS = [(0.85, 0.1, 0.1), (0.1, 0.55, 0.1), (0.1, 0.2, 0.9), (0.8, 0.5, 0.0), (0.6, 0.1, 0.7), (0.0, 0.6, 0.7), (0.5, 0.35, 0.1)]


@APP.function(image=render_image, volumes={D: VOL}, timeout=1200, cpu=2)
def semviz(names: list, s: float = 12.0):
    """Class identification aid: each drawing rendered with every labelled primitive coloured by its semantic id and
    the id printed at each instance's centre. Returns [(name, jpg bytes, {sem: [n instances, median w, median h]})]."""
    import cairocffi as cairo, cv2, numpy as np, statistics
    VOL.reload()
    out = []
    for name in names:
        p = (glob.glob(f"{F}/raw/*/{name}.svg") or [None])[0]
        if p is None:
            continue
        (vx, vy, vw, vh), prims, texts = _parse(p)
        W, H = int(vw * s) + 1, int(vh * s) + 1
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
        ctx = cairo.Context(surf)
        ctx.set_source_rgb(1, 1, 1)
        ctx.paint()
        for polys, sem, inst, col in prims:
            ctx.set_source_rgb(*(SEM_COLORS[sem % len(SEM_COLORS)] if sem else (0.75, 0.75, 0.75)))
            ctx.set_line_width(2.0 if sem else 1.0)
            for poly in polys:
                ctx.move_to((poly[0][0] - vx) * s, (poly[0][1] - vy) * s)
                for x, y in poly[1:]:
                    ctx.line_to((x - vx) * s, (y - vy) * s)
            ctx.stroke()
        ctx.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(16)
        info = {}
        for (sem, inst), b in _instances(prims).items():
            info.setdefault(sem, []).append((b[2] - b[0], b[3] - b[1]))
            ctx.set_source_rgb(*SEM_COLORS[sem % len(SEM_COLORS)])
            ctx.rectangle((b[0] - vx) * s, (b[1] - vy) * s, (b[2] - b[0]) * s, (b[3] - b[1]) * s)
            ctx.set_line_width(1)
            ctx.stroke()
            ctx.move_to((b[0] - vx) * s, (b[1] - vy) * s - 2)
            ctx.show_text(str(sem))
        stuff = {}
        for polys, sem, inst, col in prims:
            if sem and inst < 0:
                stuff[sem] = stuff.get(sem, 0) + 1
        img = np.frombuffer(surf.get_data(), np.uint8).reshape(H, surf.get_stride() // 4, 4)[:, :W, :3]
        out.append((name, cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes(),
                    dict(things={k: [len(v), round(statistics.median(a for a, _ in v), 2), round(statistics.median(b for _, b in v), 2)] for k, v in sorted(info.items())},
                         stuff_primitives=stuff, texts=[t[:4] for t in texts[:6]])))
    return out


@APP.function(image=render_image, volumes={D: VOL}, timeout=1800, cpu=4)
def gallery(sems: list, per: int = 8, scan: int = 600):
    """Class identification aid: for each semantic id, up to `per` instances from different drawings, each cropped with
    its surroundings (instance in red, everything else black). Returns [(sem, jpg bytes, size stats in SVG units)]."""
    import cairocffi as cairo, cv2, numpy as np, statistics
    VOL.reload()
    fs = sorted(glob.glob(f"{F}/raw/*/*.svg"))
    fs = fs[:: max(1, len(fs) // scan)]
    found = {s: [] for s in sems}
    for f in fs:
        if all(len(v) >= per for v in found.values()):
            break
        parsed = _parse(f)
        inst = _instances(parsed[1])
        taken = set()
        for (sem, i), b in inst.items():
            if sem in found and len(found[sem]) < per and sem not in taken:
                found[sem].append((parsed, (sem, i), b, os.path.basename(f)))
                taken.add(sem)
    out = []
    C = 260
    for sem, items in found.items():
        tiles = []
        for (vb, prims, _), key, b, fname in items:
            w, h = b[2] - b[0], b[3] - b[1]
            m = max(3.0, 0.6 * max(w, h))
            x0, y0, side = b[0] - m, b[1] - m, max(w, h) + 2 * m
            s = C / side
            surf = cairo.ImageSurface(cairo.FORMAT_RGB24, C, C)
            ctx = cairo.Context(surf)
            ctx.set_source_rgb(1, 1, 1)
            ctx.paint()
            for polys, sm, ii, col in prims:
                ctx.set_source_rgb(*((0.9, 0.1, 0.1) if (sm, ii) == key else (0.35, 0.35, 0.35)))
                ctx.set_line_width(2.0 if (sm, ii) == key else 1.0)
                for poly in polys:
                    ctx.move_to((poly[0][0] - x0) * s, (poly[0][1] - y0) * s)
                    for x, y in poly[1:]:
                        ctx.line_to((x - x0) * s, (y - y0) * s)
                ctx.stroke()
            ctx.set_source_rgb(0, 0, 0.8)
            ctx.set_font_size(13)
            ctx.move_to(3, 14)
            ctx.show_text(f"{sem} {fname[:-4]} {w:.1f}x{h:.1f}")
            tiles.append(np.frombuffer(surf.get_data(), np.uint8).reshape(C, surf.get_stride() // 4, 4)[:, :C, :3].copy())
        if not tiles:
            continue
        while len(tiles) % 4:
            tiles.append(np.full((C, C, 3), 255, np.uint8))
        rows = [np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]
        grid = np.vstack(rows)
        cv2.rectangle(grid, (0, 0), (grid.shape[1] - 1, grid.shape[0] - 1), (0, 0, 0), 1)
        sz = [max(it[2][2] - it[2][0], it[2][3] - it[2][1]) for it in items]
        out.append((sem, cv2.imencode(".jpg", grid, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes(), dict(n=len(items), median_max_side=round(statistics.median(sz), 2))))
    return out


# ---- YOLO set ------------------------------------------------------------------------------------------------------
# Our detector's classes: tools/modal_train.py CLASSES, same names in the same order (fine-tuning keeps the heads).
CLASSES = ["door", "window", "wc", "sink", "bathtub", "shower", "sauna_stove", "sauna_bench",
           "bedroom", "kitchen", "bathroom", "wc_room", "balcony", "sauna_room", "floor",
           "room", "living_room", "dining", "entry", "storage", "utility", "dressing_room", "terrace", "garage", "other_room"]
# FloorPlanCAD semantic id -> our class. Ids identified by eye with the gallery stage (instances cropped per id); this
# release numbers classes differently from the SymPoint/CADTransformer lists (1 = wall, doors start at 3).
#   3 single, 4 double, 5 sliding, 6 folding, 7 revolving, 8 rolling door | 9 window, 10 bay window, 11 blind window
#   23 sink / washbasin | 24 'bath' = shower area (tray with diagonals, shower head) | 25 bathtub | 27 squat toilet, 29 toilet
# Not labelled (background): 1 wall, 13-22 furniture/appliances (20 fridge, 22 gas stove, 26 washing machine), 28 urinal,
# 30 stairs, 31+ elevator/escalator/stuff. 12 'opening symbol' is NOT DRAWN: many of its instances look exactly like door
# swings, so leaving them on the canvas unlabelled would teach the model that door symbols are background.
FPC_TO_OURS = {3: "door", 4: "door", 5: "door", 6: "door", 7: "door", 8: "door", 9: "window", 10: "window", 11: "window",
               23: "sink", 24: "shower", 25: "bathtub", 27: "wc", 29: "wc"}
SKIP_SEMS = {12}
WALL_SEMS = {1}
TILE = 1280            # = drawqa/detect.py TILE: inference runs 1280 px tiles at native resolution
DOOR_UNITS = 9.0       # median single-door symbol (leaf + swing, id 3) in SVG units (probe: p10 6, p50 9, p90 11; 1 unit ~ 0.1 m)
DOOR_PX = (36, 109)    # door symbol size in pixels, drawn log-uniformly per canvas (CAD sheets at 150 dpi, 1:50-1:200)
KEEP_EMPTY = 0.3       # share of canvases without any of our symbols that are kept


def _plan(split, names, seed):
    """Canvases for one split: [(split, canvas id, [drawing names], px per SVG unit, seed)]. A FloorPlanCAD block is
    100 units (~10 m). Ultralytics scales every training image so its long side is imgsz (1280), so a block rendered
    smaller than that would be enlarged and its doors would no longer have the size they have at inference: every
    canvas is therefore exactly 1280 x 1280. Small scales get 2 x 2 blocks per canvas, larger ones a single block."""
    import math, random
    rng = random.Random(seed)
    names = list(names)
    rng.shuffle(names)
    jobs, i = [], 0
    while i < len(names):
        door = math.exp(rng.uniform(math.log(DOOR_PX[0]), math.log(DOOR_PX[1])))
        s = min(door / DOOR_UNITS, TILE / 100.0)
        k = 4 if s * 100 <= TILE / 2 else 1
        jobs.append((split, len(jobs), names[i:i + k], s, rng.randrange(1 << 30)))
        i += k
    return jobs


def _latinise(text, rng):
    """CJK characters -> Latin capitals (our sheets are English); digits and Latin text stay."""
    return "".join(chr(65 + rng.randrange(26)) if ord(ch) > 0x2E80 else ch for ch in text)


def _render_canvas(job, paths, write=True):
    """One canvas: draw its blocks in a plotted-sheet style, label our symbols. Returns stats (and image + labels when
    write=False)."""
    import random
    import cairocffi as cairo, cv2, numpy as np
    split, cid, names, s, seed = job
    rng = random.Random(seed)
    cell = TILE // 2 if len(names) == 4 else TILE
    # style of this sheet: line width, heavier walls or not, ink from layer colours or all black, text or not
    lw = rng.uniform(0.8, 2.2)
    wall_k = rng.choice([1.0, 1.0, 1.6, 2.2])
    ink_mode = rng.choice(["black", "black", "layers"])
    with_text = rng.random() < 0.85
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, TILE, TILE)
    ctx = cairo.Context(surf)
    if rng.random() < 0.2:
        ctx.set_antialias(cairo.ANTIALIAS_NONE)
    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.select_font_face(rng.choice(["DejaVu Sans", "DejaVu Sans Mono", "DejaVu Serif"]))
    labels, stats = [], dict(dropped_cut=0)
    for k, name in enumerate(names):
        try:
            (vx, vy, vw, vh), prims, texts = _parse(paths[name])
        except Exception:
            stats["parse_fail"] = stats.get("parse_fail", 0) + 1
            continue
        cx0, cy0 = ((k % 2) * cell, (k // 2) * cell) if len(names) == 4 else (0, 0)
        size = max(vw, vh) * s
        ox = cx0 + rng.uniform(0, max(0.0, cell - vw * s))
        oy = cy0 + rng.uniform(0, max(0.0, cell - vh * s))
        clip = (max(cx0, ox), max(cy0, oy), min(cx0 + cell, ox + vw * s), min(cy0 + cell, oy + vh * s))  # block's area on the canvas
        ctx.save()
        ctx.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
        ctx.clip()
        for polys, sem, inst, col in prims:
            if sem in SKIP_SEMS:
                continue
            if ink_mode == "black":
                g = 0.0
            else:  # layer colours as plotted in monochrome: darker layers black, light/coloured layers grey
                g = min(0.62, (0.299 * col[0] + 0.587 * col[1] + 0.114 * col[2]) / 255 * 0.8)
            ctx.set_source_rgb(g, g, g)
            ctx.set_line_width(lw * (wall_k if sem in WALL_SEMS else 1.0))
            for poly in polys:
                ctx.move_to(ox + (poly[0][0] - vx) * s, oy + (poly[0][1] - vy) * s)
                for x, y in poly[1:]:
                    ctx.line_to(ox + (x - vx) * s, oy + (y - vy) * s)
            ctx.stroke()
        if with_text:
            ctx.set_source_rgb(0, 0, 0)
            for txt, x, y, fs, m in texts:
                ctx.save()
                ctx.translate(ox - vx * s, oy - vy * s)
                ctx.scale(s, s)
                ctx.transform(cairo.Matrix(*m))
                ctx.set_font_size(fs)
                ctx.move_to(x, y)
                ctx.show_text(_latinise(txt, rng))
                ctx.restore()
        ctx.restore()
        for (sem, inst), b in _instances(prims).items():
            cls = FPC_TO_OURS.get(sem)
            if cls is None:
                continue
            X0, Y0, X1, Y1 = ox + (b[0] - vx) * s - 2, oy + (b[1] - vy) * s - 2, ox + (b[2] - vx) * s + 2, oy + (b[3] - vy) * s + 2
            a0, b0, a1, b1 = max(X0, clip[0]), max(Y0, clip[1]), min(X1, clip[2]), min(Y1, clip[3])
            if a1 - a0 < 4 or b1 - b0 < 4 or (a1 - a0) * (b1 - b0) < 0.6 * (X1 - X0) * (Y1 - Y0):
                stats["dropped_cut"] += 1  # mostly outside the block / canvas: a sliver, not labelled
                continue
            labels.append((CLASSES.index(cls), a0, b0, a1, b1))
    img = np.frombuffer(surf.get_data(), np.uint8).reshape(TILE, surf.get_stride() // 4, 4)[:, :TILE, 1].copy()
    if rng.random() < 0.15:  # a scanned / compressed copy
        img = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(35, 75)])[1], cv2.IMREAD_GRAYSCALE)
    stats.update(split=split, cid=cid, blocks=len(names), s=round(s, 3),
                 boxes=[(CLASSES[c], round(x1 - x0, 1), round(y1 - y0, 1)) for c, x0, y0, x1, y1 in labels])
    kept = bool(labels) or rng.random() < KEEP_EMPTY
    stats["kept"] = kept
    lines = "".join(f"{c} {(x0 + x1) / 2 / TILE:.6f} {(y0 + y1) / 2 / TILE:.6f} {(x1 - x0) / TILE:.6f} {(y1 - y0) / TILE:.6f}\n" for c, x0, y0, x1, y1 in labels)
    stem = f"fpc-{split}-{cid:05d}"
    if not write:
        return stats, img, lines
    if kept:
        cv2.imwrite(f"{Y}/images/{split}/{stem}.png", img, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        open(f"{Y}/labels/{split}/{stem}.txt", "w").write(lines)
    return stats


def _render_worker(args):
    job, paths = args
    try:
        return _render_canvas(job, paths)
    except Exception as e:
        return dict(split=job[0], cid=job[1], error=repr(e)[:300], kept=False, boxes=[])


def _splits():
    """FloorPlanCAD's own split: train1 + train2 = train, test = test. Val = 1 in 20 source drawings of train (blocks
    of one drawing share the prefix of their file name, so they stay on one side)."""
    import zlib
    paths, sp = {}, {"train": [], "val": [], "test": []}
    for part in ("train1", "train2", "test"):
        for f in sorted(glob.glob(f"{F}/raw/{part}/*.svg")):
            name = f"{part}_{os.path.basename(f)[:-4]}"
            paths[name] = f
            if part == "test":
                sp["test"].append(name)
            else:
                src = os.path.basename(f).split("-")[0]
                sp["val" if zlib.crc32(f"{part}/{src}".encode()) % 20 == 0 else "train"].append(name)
    return paths, sp


@APP.function(image=render_image, volumes={D: VOL}, timeout=4 * 3600, cpu=32, memory=32768)
def render(limit: int = 0):
    """All canvases of all splits -> /data/yolo_fpc (deleted and rebuilt). limit = blocks per split (0 = all)."""
    import collections, shutil, statistics
    from multiprocessing import Pool
    VOL.reload()
    shutil.rmtree(Y, ignore_errors=True)
    for s in ("train", "val", "test"):
        os.makedirs(f"{Y}/images/{s}", exist_ok=True)
        os.makedirs(f"{Y}/labels/{s}", exist_ok=True)
    paths, sp = _splits()
    jobs = []
    for split, names in sp.items():
        jobs += _plan(split, names[:limit] if limit else names, seed=len(split) * 7919)
    log = dict(started=time.ctime(), blocks={k: len(v) for k, v in sp.items()}, canvases_planned=len(jobs), done=0)
    _log("render", log)
    res = []
    with Pool(32) as p:
        for r in p.imap_unordered(_render_worker, [(j, {n: paths[n] for n in j[2]}) for j in jobs], chunksize=4):
            res.append(r)
            if len(res) % 1000 == 0:
                log["done"] = len(res)
                _log("render", log)
    stats = _stats(res, sp, limit)
    json.dump(stats, open(f"{Y}/stats.json", "w"), indent=1)
    names = "names:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASSES))
    for split in ("train", "val", "test"):
        files = sorted(glob.glob(f"{Y}/images/{split}/*.png"))
        open(f"{Y}/{split}.txt", "w").write("\n".join(files) + "\n")
    open(f"{Y}/data.yaml", "w").write(f"path: {Y}\ntrain: images/train\nval: images/val\ntest: images/test\n{names}")
    log.update(done=len(res), finished=time.ctime(), errors=[r["error"] for r in res if "error" in r][:10])
    _log("render", log)
    return stats


def _stats(res, sp, limit):
    import collections
    out = dict(source="FloorPlanCAD (floorplancad.github.io), CC BY-NC 4.0 annotations", tile=TILE, door_px=DOOR_PX,
               door_units=DOOR_UNITS, mapping={str(k): v for k, v in FPC_TO_OURS.items()}, splits={})
    for split in ("train", "val", "test"):
        rs = [r for r in res if r["split"] == split]
        kept = [r for r in rs if r["kept"]]
        boxes = collections.defaultdict(list)
        for r in kept:
            for c, w, h in r["boxes"]:
                boxes[c].append((max(w, h), min(w, h)))
        q = lambda a, p: round(sorted(a)[int(p * (len(a) - 1))], 1) if a else None
        out["splits"][split] = dict(
            blocks=min(len(sp[split]), limit) if limit else len(sp[split]), canvases=len(rs), images=len(kept),
            empty_images=sum(1 for r in kept if not r["boxes"]), errors=sum(1 for r in rs if "error" in r),
            cut_boxes_dropped=sum(r.get("dropped_cut", 0) for r in kept),
            boxes={c: len(v) for c, v in sorted(boxes.items())},
            size_px={c: dict(long_side=[q([a for a, _ in v], p) for p in (0.1, 0.5, 0.9)], short_side=[q([b for _, b in v], p) for p in (0.1, 0.5, 0.9)])
                     for c, v in sorted(boxes.items())})
    return out


@APP.function(image=render_image, volumes={D: VOL}, timeout=1200, cpu=2)
def overlays(n: int = 6, split: str = "val", seed: int = 0):
    """n rendered canvases of the built set with their label boxes drawn (class name + colour), as JPEGs."""
    import random, cv2
    VOL.reload()
    fs = sorted(glob.glob(f"{Y}/labels/{split}/*.txt"))
    fs = [f for f in fs if os.path.getsize(f) > 0]
    random.Random(seed).shuffle(fs)
    col = {"door": (0, 0, 230), "window": (230, 120, 0), "wc": (0, 160, 0), "sink": (200, 0, 200), "bathtub": (0, 140, 255), "shower": (160, 160, 0)}
    out = []
    for f in fs[:n]:
        im = cv2.cvtColor(cv2.imread(f.replace("/labels/", "/images/")[:-4] + ".png", cv2.IMREAD_GRAYSCALE), cv2.COLOR_GRAY2BGR)
        for line in open(f):
            c, cx, cy, w, h = line.split()
            c, cx, cy, w, h = CLASSES[int(c)], float(cx) * TILE, float(cy) * TILE, float(w) * TILE, float(h) * TILE
            cv2.rectangle(im, (int(cx - w / 2), int(cy - h / 2)), (int(cx + w / 2), int(cy + h / 2)), col.get(c, (0, 0, 0)), 2)
            cv2.putText(im, c, (int(cx - w / 2), int(cy - h / 2) - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col.get(c, (0, 0, 0)), 1)
        out.append((os.path.basename(f)[:-4], cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()))
    return out


@APP.function(image=dl_image, volumes={D: VOL}, timeout=600)
def read_bytes(path: str):
    VOL.reload()
    return open(f"{D}/{path}", "rb").read() if os.path.exists(f"{D}/{path}") else None


@APP.local_entrypoint()
def main(stage: str = "status", n: int = 6, limit: int = 0):
    if stage == "download":
        print("spawned download", download.spawn().object_id)
        return
    if stage == "status":
        for name in ("floorplancad/download.json", "floorplancad/render.json", "yolo_fpc/stats.json"):
            b = read_bytes.remote(name)
            print(f"--- {name}:", b.decode()[:6000] if b else "nothing yet")
        return
    if stage == "cat":  # DRAWQA_PATH=runs/d26l/args.yaml (relative to /data), read-only
        b = read_bytes.remote(os.environ["DRAWQA_PATH"])
        print(b.decode(errors="replace")[-6000:] if b else "missing")
        return
    if stage == "render":  # --limit = blocks per split (0 = all); spawned (run with --detach), poll with --stage status
        print("spawned render", render.spawn(limit).object_id)
        return
    if stage == "overlays":  # DRAWQA_SPLIT=val ; JPEGs with label boxes to DRAWQA_OUT
        out = os.environ.get("DRAWQA_OUT", ".")
        os.makedirs(out, exist_ok=True)
        for name, jpg in overlays.remote(n, os.environ.get("DRAWQA_SPLIT", "val"), int(os.environ.get("DRAWQA_SEED", 0))):
            open(f"{out}/{name}.jpg", "wb").write(jpg)
            print("wrote", f"{out}/{name}.jpg")
        return
    if stage == "peek":
        print(json.dumps(peek.remote(), indent=1)[:12000])
        return
    if stage == "probe":
        print(json.dumps(probe.remote(n), indent=None))
        return
    if stage == "semviz":  # DRAWQA_NAMES=0000-0002,0000-0003 ; images go to DRAWQA_OUT
        out = os.environ.get("DRAWQA_OUT", ".")
        os.makedirs(out, exist_ok=True)
        for name, jpg, info in semviz.remote(os.environ["DRAWQA_NAMES"].split(",")):
            open(f"{out}/sem_{name}.jpg", "wb").write(jpg)
            print(name, json.dumps(info))
        return
    if stage == "gallery":  # DRAWQA_SEMS=3,4,5 ; one grid image per semantic id to DRAWQA_OUT
        out = os.environ.get("DRAWQA_OUT", ".")
        os.makedirs(out, exist_ok=True)
        for sem, jpg, info in gallery.remote([int(v) for v in os.environ["DRAWQA_SEMS"].split(",")], n):
            open(f"{out}/gal_{sem:02d}.jpg", "wb").write(jpg)
            print(sem, info)
        return
    print("unknown stage", stage)
