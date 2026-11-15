"""Walls and room outlines on a scanned or rendered plan (a second trained model, next to the element detector).

    outlines(image_bgr) -> dict(walls=[...], rooms=[...])
    wall: dict(polygon, axis=[x0, y0, x1, y1], thickness_px, conf)     axis = centre line of the wall segment
    room: dict(polygon, area_px, outdoor, conf)                        outdoor = balcony, terrace and the like

Weights: models/segmenter.pt (override DRAWQA_SEG_WEIGHTS), trained by tools/modal_train.py --stage train_seg on
CubiCasa5k's own wall and room polygons. The detector stays the finder of doors and windows; this model only adds the
walls they sit in and the outlines areas are measured from. Everything it returns is "detected", never exact.
"""
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
WEIGHTS = os.environ.get("DRAWQA_SEG_WEIGHTS", os.path.join(ROOT, "models", "segmenter.pt"))
CONF = {"wall": 0.25, "room": 0.35, "outdoor": 0.35}  # not tuned on any answer key
_model = None


def available():
    return os.path.exists(WEIGHTS)


def measured():
    """Held-out scores of these weights (written by `tools/modal_train.py --stage seg_eval` next to the weights file):
    share of rooms found, room-area error, and whether outlines run small or large. {} when never measured."""
    import json
    f = os.path.splitext(WEIGHTS)[0] + ".eval.json"
    return json.load(open(f)) if os.path.exists(f) else {}


def area_bias():
    """Median of (outlined area / annotated area - 1) on held-out plans. Outlines of this kind of model run a few
    per cent small; areas are divided by (1 + bias) so that the typical room comes out right."""
    return float((measured().get("room_area_signed") or {}).get("median") or 0.0)


def _load():
    global _model
    if _model is None:
        from ultralytics import YOLO
        _model = YOLO(WEIGHTS)
    return _model


def _area(poly):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]))) / 2


def wall_axis(poly):
    """Centre line and thickness of a wall polygon: the long axis of its smallest enclosing rotated rectangle."""
    import cv2, numpy as np
    box = cv2.boxPoints(cv2.minAreaRect(np.array(poly, np.float32)))
    d01, d12 = np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[1] - box[2])
    a, b = ((box[0] + box[1]) / 2, (box[2] + box[3]) / 2) if d01 < d12 else ((box[1] + box[2]) / 2, (box[3] + box[0]) / 2)
    return [round(float(v), 1) for v in (*a, *b)], round(float(min(d01, d12)), 1)


def pick_rooms(cands, shape, sure, fill=None, max_overlap=0.2):
    """Which room outlines to keep. cands = [dict(polygon, conf, ...)], shape = (height, width).
    Confidence >= sure: kept, as before. With fill set, an outline between fill and sure is also kept if at most
    max_overlap of it lies inside rooms already kept (most confident first): large open rooms are often found only
    with low confidence, and leaving them out costs more area than a doubtful outline adds. Used by the local path
    and by the held-out evaluation (tools/modal_train.py --stage seg_eval), so both apply one rule."""
    import cv2, numpy as np
    keep = [c for c in cands if c["conf"] >= sure]
    if fill is None:
        return keep
    k = min(1.0, 800 / max(shape[:2]))
    h, w = int(shape[0] * k) + 1, int(shape[1] * k) + 1
    mask = lambda c: cv2.fillPoly(np.zeros((h, w), np.uint8), [np.int32(np.array(c["polygon"], float) * k)], 1).astype(bool)
    taken = np.zeros((h, w), bool)
    for c in keep:
        taken |= mask(c)
    for c in sorted((c for c in cands if fill <= c["conf"] < sure), key=lambda c: -c["conf"]):
        m = mask(c)
        if m.sum() and (m & taken).sum() <= max_overlap * m.sum():
            keep.append(c)
            taken |= m
    return keep


# Measured on 200 held-out plans (seg1, mid-training checkpoint, 2026-11-14): with 0.15 the summed indoor area per plan
# is off by 3.9% median / 19.7% at the 90th percentile, against 4.4% / 22.5% with confident outlines only; rooms
# found 85.0% against 84.3%; the error of a single outlined room is unchanged. None = only confident outlines.
FILL = 0.15


def outlines(img, imgsz=1280):
    """The whole sheet is read at once (fitted to imgsz), as in training. Masks stay at network resolution: full-size
    masks for a hundred walls would not fit in this machine's memory."""
    from . import remote
    if remote.enabled():  # on the Modal GPU (drawqa/remote.py); None = not reachable, run here
        r = remote.try_call("outlines", img, imgsz=imgsz)
        if r is not None:
            return r
    return _outlines(img, imgsz)


def outlines_tiled(img, tile=1280, overlap=0.25):
    """Big sheets: walls from native-resolution tiles. Read whole, a 10,000 px sheet is shrunk about 8x and its walls
    vanish (the model then draws shapes across rooms); in 1280 px tiles a wall is about as many pixels thick as in the
    training scans. Rooms are not taken from tiles (tile edges cut them). Walls are shifted to page coordinates; a
    piece lying mostly inside an already kept wall (the overlap of two tiles) is dropped."""
    from . import remote
    if remote.enabled():
        r = remote.try_call("outlines_tiled", img, tile=tile, overlap=overlap)
        if r is not None:
            return r
    from shapely.geometry import Polygon
    from shapely.strtree import STRtree
    from .detect import tiles
    H, W = img.shape[:2]
    found = []
    for x, y in tiles(W, H, size=tile, overlap=overlap):
        part = img[y:y + tile, x:x + tile]
        if part.size == 0 or part.mean() > 250:  # blank paper
            continue
        for w in _outlines(part, tile)["walls"]:
            P = [[round(px + x, 1), round(py + y, 1)] for px, py in w["polygon"]]
            ax = w["axis"]
            found.append(dict(w, polygon=P, axis=[round(ax[0] + x, 1), round(ax[1] + y, 1), round(ax[2] + x, 1), round(ax[3] + y, 1)]))
    geoms = [Polygon(w["polygon"]).buffer(0) for w in found]
    order = sorted(range(len(found)), key=lambda i: -geoms[i].area)  # big pieces first
    kept, kg = [], []
    for i in order:
        g = geoms[i]
        if g.is_empty or g.area <= 0:
            continue
        if kg:
            tree = STRtree(kg)
            if any(g.intersection(kg[j]).area > 0.6 * g.area for j in tree.query(g)):
                continue
        kept.append(found[i]); kg.append(g)
    return dict(walls=kept, rooms=[])


def _outlines(img, imgsz=1280):
    import cv2, numpy as np
    m = _load()
    r = m.predict(img, imgsz=imgsz, conf=min(CONF.values()), iou=0.6, max_det=600, retina_masks=False, verbose=False)[0]
    walls, rooms, bias = [], [], area_bias()
    if r.masks is None:
        return dict(walls=walls, rooms=rooms)
    for poly, c, s in zip(r.masks.xy, r.boxes.cls.tolist(), r.boxes.conf.tolist()):
        cls = m.names[int(c)]
        if s < (CONF["wall"] if cls == "wall" else (FILL if FILL is not None else CONF.get(cls, 0.3))) or len(poly) < 3:
            continue
        eps = 1.5 if cls == "wall" else 2.5  # pixels: drop the mask's staircase without moving corners
        pts = cv2.approxPolyDP(np.array(poly, np.float32).reshape(-1, 1, 2), eps, True).reshape(-1, 2)
        if len(pts) < 3:
            continue
        P = [[round(float(x), 1), round(float(y), 1)] for x, y in pts]
        if cls == "wall":
            axis, thick = wall_axis(P)
            walls.append(dict(polygon=P, axis=axis, thickness_px=thick, conf=round(float(s), 3)))
        else:
            raw = _area(P)
            rooms.append(dict(polygon=P, area_px=round(raw / (1 + bias), 1), area_px_raw=round(raw, 1), outdoor=cls == "outdoor", conf=round(float(s), 3)))
    rooms = pick_rooms(rooms, img.shape, CONF["room"], FILL)
    return dict(walls=walls, rooms=rooms)


def _seg_dist(px, py, x0, y0, x1, y1):
    """Distance from a point to a segment, and how far along the segment (0..1) its nearest point lies."""
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L2))
    return ((px - x0 - t * dx) ** 2 + (py - y0 - t * dy) ** 2) ** 0.5, t


def attach_openings(openings, walls):
    """Give each door and window the wall it sits in: nearest wall centre line within reach of the opening's box.
    Adds wall (id), wall_distance_px, along_px (distance from the wall's start) and width_px (its extent along the
    wall). Openings no wall comes near keep wall=None; that is reported, not hidden. Returns how many were attached."""
    n = 0
    for o in openings:
        x0, y0, x1, y1 = o["bbox"]
        cx, cy, long_side = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0)
        best = None
        for w in walls:
            d, t = _seg_dist(cx, cy, *w["axis"])
            if d <= max(w["thickness_px"], 0.75 * long_side) and (best is None or d < best[0]):
                best = (d, t, w)
        if best is None:
            o["wall"] = None
            continue
        d, t, w = best
        ax0, ay0, ax1, ay1 = w["axis"]
        L = ((ax1 - ax0) ** 2 + (ay1 - ay0) ** 2) ** 0.5 or 1.0
        ux, uy = abs(ax1 - ax0) / L, abs(ay1 - ay0) / L
        o.update(wall=w["id"], wall_distance_px=round(d, 1), along_px=round(t * L, 1),
                 width_px=round((x1 - x0) * ux + (y1 - y0) * uy, 1))
        n += 1
    return n


def tidy_walls(walls, min_fill=0.7, inside=0.6):
    """Clean wall shapes for drawing and measuring. The model's mask outlines are ragged (spikes where walls meet) and
    end in the slanted corner cuts of CubiCasa's labels. A straight wall (its mask fills >= min_fill of its smallest
    rotated rectangle) is given that rectangle; anything else keeps its outline. A piece that lies mostly (>= inside)
    within a surer wall is dropped. Returns the kept walls."""
    import cv2, numpy as np
    from shapely.geometry import Polygon
    out = []
    for w in walls:
        P = np.array(w["polygon"], np.float32)
        (_, _), (rw, rh), _ = cv2.minAreaRect(P)
        rect = cv2.boxPoints(cv2.minAreaRect(P))
        ra = float(cv2.contourArea(rect))
        try:  # an outline that crosses itself (a mask in two parts joined by a thin bridge) is measured once repaired
            pa = Polygon(w["polygon"]).buffer(0).area
        except Exception:
            pa = 0.0
        w = dict(w)
        if ra > 0 and (min(rw, rh) <= 0.2 * max(rw, rh) or pa / ra >= min_fill):  # long and thin, or nearly fills it
            w["polygon"] = [[round(float(x), 1), round(float(y), 1)] for x, y in rect]
        out.append(w)
    from shapely.strtree import STRtree
    out.sort(key=lambda w: -w.get("conf", 0))
    geoms = []
    for w in out:
        try:
            geoms.append(Polygon(w["polygon"]).buffer(0))
        except Exception:
            geoms.append(Polygon())
    tree, keep = STRtree(geoms), set()
    for i, g in enumerate(geoms):
        if g.is_empty:
            continue
        if any(j in keep and g.intersection(geoms[j]).area >= inside * g.area for j in tree.query(g)):
            continue
        keep.add(i)
    return [w for i, w in enumerate(out) if i in keep]


def trim_to_ink(walls, gray, run=3):
    """Pull each wall's ends back to where the drawing has ink. At outer corners the model often runs both walls
    past the corner (a '+' instead of an 'L'), into blank paper. For every pixel step along the centre line we look
    across the wall's whole thickness (so hollow double-line walls count as inked) and cut the blank ends off; gaps
    inside a wall (doors, windows) are left alone. A wall the drawing barely inks (light or coloured fills) is left
    as it is, since the test would not be reliable there."""
    import numpy as np
    H, W = gray.shape[:2]
    thr = min(160.0, 0.6 * float(np.median(gray)))
    out = []
    for w in walls:
        ax0, ay0, ax1, ay1 = w["axis"]
        L = ((ax1 - ax0) ** 2 + (ay1 - ay0) ** 2) ** 0.5
        if L < 2 * run:
            out.append(w)
            continue
        ux, uy = (ax1 - ax0) / L, (ay1 - ay0) / L
        half = max(2.0, w.get("thickness_px", 4) / 2 + 1)
        s = np.arange(0, L + 1)
        t = np.arange(-half, half + 1)
        X = np.clip(np.round(ax0 + ux * s[:, None] - uy * t[None, :]).astype(int), 0, W - 1)
        Y = np.clip(np.round(ay0 + uy * s[:, None] + ux * t[None, :]).astype(int), 0, H - 1)
        inked = (gray[Y, X] < thr).any(axis=1)
        if inked.mean() < 0.5:
            out.append(w)
            continue
        ok = np.convolve(inked.astype(int), np.ones(run, int), "valid") == run  # runs of `run` inked steps
        idx = np.nonzero(ok)[0]
        if not len(idx):
            out.append(w)
            continue
        a, b = float(idx[0]), float(min(L, idx[-1] + run - 1))
        if a <= 1 and b >= L - 1:
            out.append(w)
            continue
        th = w.get("thickness_px", 4) / 2
        axis = [round(v, 1) for v in (ax0 + ux * a, ay0 + uy * a, ax0 + ux * b, ay0 + uy * b)]
        rect = [[round(axis[0] - uy * k * th, 1), round(axis[1] + ux * k * th, 1)] for k in (1, -1)] + \
               [[round(axis[2] - uy * k * th, 1), round(axis[3] + ux * k * th, 1)] for k in (-1, 1)]
        out.append(dict(w, axis=axis, polygon=rect if len(w["polygon"]) == 4 else w["polygon"], trimmed_px=round(a + (L - b), 1)))
    return out


def merge_collinear(walls, gap_px=2.0):
    """One wall drawn as several overlapping or touching pieces on the same line (the model splits long walls, and
    tiles overlap) -> one wall, so its length is counted once. Pieces merge when they run the same way, lie on the
    same line (within half a thickness) and overlap or touch along it. Walls that only meet at a corner stay apart."""
    from shapely.geometry import Polygon
    from shapely.strtree import STRtree

    def unit(w):
        ax0, ay0, ax1, ay1 = w["axis"]
        L = ((ax1 - ax0) ** 2 + (ay1 - ay0) ** 2) ** 0.5 or 1.0
        return (ax1 - ax0) / L, (ay1 - ay0) / L, L

    ws = [dict(w) for w in walls]
    changed = True
    while changed:
        changed = False
        geoms = [Polygon(w["polygon"]).buffer(gap_px) for w in ws]
        tree, gone = STRtree(geoms), set()
        for i, a in enumerate(ws):
            if i in gone:
                continue
            ux, uy, La = unit(a)
            for j in tree.query(geoms[i]):
                j = int(j)
                if j <= i or j in gone:
                    continue
                b = ws[j]
                vx, vy, Lb = unit(b)
                if abs(ux * vy - uy * vx) > 0.05:  # not parallel
                    continue
                ta, tb = a["thickness_px"], b["thickness_px"]
                if max(ta, tb) > 1.6 * min(ta, tb) + 2:  # a chunky block next to a thin wall is not the same wall
                    continue
                px, py = b["axis"][0] - a["axis"][0], b["axis"][1] - a["axis"][1]
                if abs(px * uy - py * ux) > min(ta, tb) / 2 + 1:  # not on the same line
                    continue
                s = [0.0, La] + [(x - a["axis"][0]) * ux + (y - a["axis"][1]) * uy for x, y in (b["axis"][:2], b["axis"][2:])]
                if min(s[2:]) > La + gap_px or max(s[2:]) < -gap_px:  # parallel on the same line but apart along it
                    continue
                lo, hi = min(s), max(s)
                base = a if La >= Lb else b  # the longer piece gives the line
                bx, by = base["axis"][0] - ((base["axis"][0] - a["axis"][0]) * ux + (base["axis"][1] - a["axis"][1]) * uy) * ux, \
                         base["axis"][1] - ((base["axis"][0] - a["axis"][0]) * ux + (base["axis"][1] - a["axis"][1]) * uy) * uy
                th = max(a["thickness_px"], b["thickness_px"])
                axis = [round(v, 1) for v in (bx + ux * lo, by + uy * lo, bx + ux * hi, by + uy * hi)]
                h = th / 2
                rect = [[round(axis[0] - uy * h, 1), round(axis[1] + ux * h, 1)], [round(axis[0] + uy * h, 1), round(axis[1] - ux * h, 1)],
                        [round(axis[2] + uy * h, 1), round(axis[3] - ux * h, 1)], [round(axis[2] - uy * h, 1), round(axis[3] + ux * h, 1)]]
                ws[i] = a = dict(a, axis=axis, thickness_px=th, polygon=rect, conf=max(a.get("conf", 0), b.get("conf", 0)),
                                 merged=a.get("merged", 1) + b.get("merged", 1))
                ux, uy, La = unit(a)
                gone.add(j)
                changed = True
        ws = [w for k, w in enumerate(ws) if k not in gone]
    return ws


def wall_outline(walls, simplify_px=0.5):
    """All wall pieces as one shape for drawing: the outline rings of their union (outer edges and the edges around
    each room), so corners and junctions show as one connected wall instead of overlapping rectangles."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    polys = []
    for w in walls:
        try:
            g = Polygon(w["polygon"]).buffer(0)
            if not g.is_empty:
                polys.append(g)
        except Exception:
            pass
    if not polys:
        return []
    u = unary_union(polys).simplify(simplify_px)
    rings = []
    for g in getattr(u, "geoms", [u]):
        if g.geom_type != "Polygon":
            continue
        for r in [g.exterior, *g.interiors]:
            rings.append([[round(x, 1), round(y, 1)] for x, y in list(r.coords)[:-1]])
    return rings


def wall_perimeters(walls):
    """Perimeters of the joined walls, measured straight across doors and windows (call it before cut_openings: the
    model's walls run through openings). Returns px lengths: outside = outer face of the largest connected wall
    network; inside = the inner face around each space that walls close all round, largest first; pieces = how many
    separate wall networks there are (more than one means the model missed a wall somewhere, and a space it
    should close is open, so it has no inside perimeter)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    polys = []
    for w in walls:
        try:
            g = Polygon(w["polygon"]).buffer(0)
            if not g.is_empty:
                polys.append(g)
        except Exception:
            pass
    if not polys:
        return None
    u = unary_union([p.buffer(1.0) for p in polys]).buffer(-1.0)  # close hairline gaps where pieces meet
    parts = sorted((g for g in getattr(u, "geoms", [u]) if g.geom_type == "Polygon"), key=lambda g: -g.area)
    if not parts:
        return None
    main = parts[0]
    ring = lambda r: [[round(x, 1), round(y, 1)] for x, y in list(r.coords)[:-1]]
    rooms = sorted((r for r in main.interiors if Polygon(r).area > 400), key=lambda r: -r.length)  # > 20x20 px
    return dict(outside_px=round(main.exterior.length, 1), inside_px=[round(r.length, 1) for r in rooms], pieces=len(parts),
                outside_ring=ring(main.exterior), inside_rings=[ring(r) for r in rooms])


def cut_openings(walls, openings, min_piece_px=3.0):
    """Cut each wall at the doors and windows attached to it (attach_openings first) -> list of solid wall pieces.

    The model learned CubiCasa's labels, where a wall is one polygon from corner to corner with its doors and windows
    lying inside it, so it draws walls straight through openings. Each piece keeps the wall's thickness and conf and
    gets run = the id of the whole wall it came from (the id openings refer to). A wall with no opening stays one piece
    (id unchanged, run = its id)."""
    from shapely.geometry import Polygon
    by_wall = {}
    for o in openings:
        if o.get("wall") is not None and o.get("width_px"):
            by_wall.setdefault(o["wall"], []).append(o)
    out = []
    for w in walls:
        ops = by_wall.get(w["id"])
        if not ops:
            out.append(dict(w, run=w["id"]))
            continue
        ax0, ay0, ax1, ay1 = w["axis"]
        L = ((ax1 - ax0) ** 2 + (ay1 - ay0) ** 2) ** 0.5
        if L < 1:
            out.append(dict(w, run=w["id"]))
            continue
        ux, uy = (ax1 - ax0) / L, (ay1 - ay0) / L
        gaps = sorted((max(0.0, o["along_px"] - o["width_px"] / 2), min(L, o["along_px"] + o["width_px"] / 2)) for o in ops)
        solid, start = [], 0.0
        for a, b in gaps:
            if a > start:
                solid.append((start, a))
            start = max(start, b)
        if start < L:
            solid.append((start, L))
        try:
            poly = Polygon(w["polygon"]).buffer(0)
        except Exception:
            poly = None
        reach = 3 * w["thickness_px"] + 50  # the strip reaches across the whole wall band
        k = 0
        for a, b in solid:
            if b - a < min_piece_px:
                continue
            ea, eb = (a if a > 0 else -reach), (b if b < L else L + reach)  # do not clip the wall's own ends
            P = None
            if poly is not None and not poly.is_empty:
                strip = Polygon([(ax0 + ux * s + uy * t, ay0 + uy * s - ux * t) for s, t in ((ea, -reach), (eb, -reach), (eb, reach), (ea, reach))])
                part = poly.intersection(strip)
                if not part.is_empty:
                    part = max(getattr(part, "geoms", [part]), key=lambda g: g.area)
                    if part.geom_type == "Polygon" and part.area > 0:
                        P = [[round(float(x), 1), round(float(y), 1)] for x, y in list(part.exterior.coords)[:-1]]
            axis = [round(v, 1) for v in (ax0 + ux * a, ay0 + uy * a, ax0 + ux * b, ay0 + uy * b)]
            out.append(dict(w, id=f"{w['id']}.{k}", run=w["id"], axis=axis, polygon=P or w["polygon"],
                            openings=[o["id"] for o in ops]))
            k += 1
    return out


def _inside(px, py, poly):
    hit = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > py) != (y1 > py) and px < (x1 - x0) * (py - y0) / (y1 - y0) + x0:
            hit = not hit
    return hit


def outline_rooms(rooms, found):
    """Give the detector's typed room boxes their outlines, and keep outlines no box claimed as untyped rooms.
    A box takes the outline that contains its centre (the largest share of the box if several do).
    Returns (rooms with polygon/area_px where found, number of typed rooms that got an outline)."""
    taken, n = set(), 0
    for r in rooms:
        if r.get("polygon") or not r.get("bbox"):
            continue
        x0, y0, x1, y1 = r["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        cands = [(abs(f["area_px"] - (x1 - x0) * (y1 - y0)), i) for i, f in enumerate(found) if i not in taken and _inside(cx, cy, f["polygon"])]
        if not cands:
            continue
        i = min(cands)[1]
        taken.add(i)
        r.update(polygon=found[i]["polygon"], area_px=found[i]["area_px"], outline_conf=found[i]["conf"],
                 outdoor=bool(found[i]["outdoor"] or r.get("cls") in ("balcony", "terrace")))
        n += 1
    extra = []
    for i, f in enumerate(found):
        if i not in taken:
            xs, ys = [p[0] for p in f["polygon"]], [p[1] for p in f["polygon"]]
            extra.append(dict(id=f"outline{i}", cls="unlabelled", bbox=[min(xs), min(ys), max(xs), max(ys)], polygon=f["polygon"],
                              area_px=f["area_px"], outline_conf=f["conf"], conf=f["conf"], outdoor=f["outdoor"]))
    return rooms + extra, n
