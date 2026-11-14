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
