"""Element detector for rendered or scanned drawings (runs locally on CPU / Apple GPU).

    detect(image_bgr) -> [dict(cls, bbox=[x0,y0,x1,y1], conf)]
Weights: models/detector.pt (override DRAWQA_WEIGHTS); per-class thresholds tuned for exact counts are read from
models/detector.thresholds.json when present. Sheets larger than one tile are processed in overlapping tiles so that
small symbols keep their native size, and duplicates along tile borders are merged.
"""
import json, os

ROOT = os.path.join(os.path.dirname(__file__), "..")
WEIGHTS = os.environ.get("DRAWQA_WEIGHTS", os.path.join(ROOT, "models", "detector.pt"))
TILE = 1280
TILE_BATCH = int(os.environ.get("DRAWQA_TILE_BATCH", 4))  # tiles per forward pass (memory, not accuracy)
_model = _thr = None


def available():
    return os.path.exists(WEIGHTS)


def tuned():
    """The tune stage's output for these weights: thresholds, merge rules, whether 4-orientation detection was used."""
    global _thr
    if _thr is None:
        f = os.path.splitext(WEIGHTS)[0] + ".thresholds.json"
        _thr = json.load(open(f)) if os.path.exists(f) else {}
    return _thr


def thresholds():
    return tuned().get("thresholds", {})


def tiles(w, h, size=TILE, overlap=0.25):
    def axis(n):
        if n <= size:
            return [0]
        step = int(size * (1 - overlap))
        return sorted(set(list(range(0, n - size, step)) + [n - size]))
    return [(x, y) for y in axis(h) for x in axis(w)]


def nms(dets, iou_thr=0.3):
    """Class-wise merge of [(cls, bbox, conf)] from overlapping tiles."""
    out = []
    for c in {d[0] for d in dets}:
        keep = []
        for d in sorted((d for d in dets if d[0] == c), key=lambda d: -d[2]):
            b = d[1]
            for k in keep:
                q = k[1]
                iw, ih = min(b[2], q[2]) - max(b[0], q[0]), min(b[3], q[3]) - max(b[1], q[1])
                if iw > 0 and ih > 0:
                    inter = iw * ih
                    ab, aq = (b[2] - b[0]) * (b[3] - b[1]), (q[2] - q[0]) * (q[3] - q[1])
                    if inter / (ab + aq - inter) > iou_thr or inter / max(min(ab, aq), 1e-9) > 0.5:
                        break
            else:
                keep.append(d)
        out += keep
    return out


def _load():
    global _model
    if _model is None:
        from ultralytics import YOLO
        _model = YOLO(WEIGHTS)
    return _model


def fuse(runs, iou_thr=0.35):
    """Merge detections of one image seen in several orientations; score = mean confidence over all orientations."""
    n, clusters = len(runs), []
    for oi, dets in enumerate(runs):
        for c, b, s in sorted(dets, key=lambda d: -d[2]):
            best = None
            for cl in clusters:
                if cl[0] != c or oi in cl[3]:
                    continue
                q = cl[1][0]
                iw, ih = min(b[2], q[2]) - max(b[0], q[0]), min(b[3], q[3]) - max(b[1], q[1])
                if iw <= 0 or ih <= 0:
                    continue
                ab, aq = (b[2] - b[0]) * (b[3] - b[1]), (q[2] - q[0]) * (q[3] - q[1])
                iou = iw * ih / (ab + aq - iw * ih)
                if (iou > iou_thr or iw * ih / max(min(ab, aq), 1e-9) > 0.6) and (best is None or iou > best[0]):
                    best = (iou, cl)
            if best:
                best[1][1].append(b); best[1][2].append(s); best[1][3].add(oi)
            else:
                clusters.append([c, [b], [s], {oi}])
    out = []
    for c, boxes, confs, _ in clusters:
        w = sum(confs)
        out.append((c, [sum(b[i] * s for b, s in zip(boxes, confs)) / w for i in range(4)], w / n))
    return out


MIN_DOOR_PX = 30  # below this (at network scale) symbols are too small to read from the whole sheet
TTA_MAX_TILES = int(os.environ.get("DRAWQA_TTA_MAX_TILES", 30))  # above this many tiles, no four-orientation fusion
last_info = {}  # how the last detect() call ran: tta, tiles, note (for the answer's basis)


def _one(m, img, imgsz, tile="auto"):
    """Whole sheet first; tiles at native resolution only if its doors come out tiny or none are found
    (same rule as in training-time evaluation, tools/modal_train.py)."""
    names = m.names
    h, w = img.shape[:2]
    conv = lambda r, dx=0, dy=0: [(names[int(c)], [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy], float(s))
                                  for b, c, s in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())]
    kw = dict(imgsz=imgsz, conf=0.05, iou=0.6, max_det=600, verbose=False)
    whole = conv(m.predict(img, **kw)[0])
    if tile == "never" or max(w, h) <= TILE * 1.2:
        return whole
    if tile == "auto":
        k = imgsz / max(w, h)
        doors = sorted(max(b[2] - b[0], b[3] - b[1]) * k for c, b, s in whole if c == "door" and s >= 0.3)
        if len(doors) >= 2 and doors[len(doors) // 2] >= MIN_DOOR_PX:
            return whole
    dets, tl = [], tiles(w, h)
    for i in range(0, len(tl), TILE_BATCH):  # a large sheet is dozens of tiles; one batch of all of them needs gigabytes
        part = tl[i:i + TILE_BATCH]
        for (x, y), r in zip(part, m.predict([img[y:y + TILE, x:x + TILE] for x, y in part], **kw)):
            dets += conv(r, x, y)
    return nms(dets)


def detect(img, imgsz=1280, raw=False, tta=None):
    """raw=True returns everything above conf 0.05; otherwise the per-class thresholds are applied.
    tta=True runs the four 90-degree orientations and fuses them (default: DRAWQA_TTA=1, or whatever the
    thresholds file says the thresholds were tuned with)."""
    from . import remote
    if remote.enabled():  # on the Modal GPU (drawqa/remote.py); None = not reachable, run here
        r = remote.try_call("detect", img, imgsz=imgsz, raw=raw, tta=tta)
        if r is not None:
            return r
    import numpy as np
    m = _load()
    h, w = img.shape[:2]
    if tta is None:
        tta = os.environ.get("DRAWQA_TTA") == "1" or bool(tuned().get("tta"))
    # a very large sheet (a 14000 px school plan is ~165 tiles, minutes per orientation): one orientation only, said so
    n_tiles = len(tiles(w, h)) if max(w, h) > TILE * 1.2 else 1
    last_info.clear()
    last_info.update(tta=tta, tiles=n_tiles, note="")
    if tta and n_tiles > TTA_MAX_TILES:
        tta = False
        last_info.update(tta=False, note=f"the detector looked at this sheet in one orientation only ({n_tiles} tiles; all four "
                                         "would take about four times as long), while its thresholds were tuned on four, so "
                                         "it may find somewhat fewer symbols than on smaller drawings")
    if not tta:
        dets = _one(m, img, imgsz)
    else:
        runs = []
        for k in range(4):
            back = []
            for c, (x0, y0, x1, y1), s in _one(m, np.ascontiguousarray(np.rot90(img, k)), imgsz):
                b = [x0, y0, x1, y1] if k == 0 else [w - y1, x0, w - y0, x1] if k == 1 else [w - x1, h - y1, w - x0, h - y0] if k == 2 else [y0, h - x1, y1, h - x0]
                back.append((c, b, s))
            runs.append(back)
        dets = fuse(runs)
    out = [dict(cls=c, bbox=[round(v, 1) for v in b], conf=round(s, 3)) for c, b, s in dets if s >= 0.05]
    if raw:
        return out
    from .postprocess import apply
    return apply(out, thresholds(), tuned().get("rules"))
