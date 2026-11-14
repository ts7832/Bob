"""Room outlines on an image: grow each detected room out to its walls.

    trace_rooms(img_bgr, rooms, doors, windows) -> one result per room, in order:
        dict(polygon=[[x, y], ...], mode="walls" | "lines", area_px=...)   the traced outline, or
        dict(polygon=None, why="...")                                      when no closed outline was found

The detector gives a rectangle per room. The image itself says where the walls are: everything the paper-coloured
area inside that rectangle connects to, without crossing ink, is the room. Detected doors and windows close the gaps
they leave in the walls. Two readings of "ink" are tried per room:
    walls : only thick strokes (thin lines such as tile hatching, furniture and dimension lines are ignored)
    lines : every dark line (for drawings whose walls are thin lines)
A trace is rejected, never guessed, when the region runs off the sheet, spills far outside the detector's rectangle
(an open doorway or an open-plan space), or covers too little of it.
"""
import cv2
import numpy as np

MIN_COVER = 0.45   # the region must fill at least this share of the detector's rectangle
MIN_INSIDE = 0.80  # and at least this share of the region must lie inside the (padded) rectangle


def ink_mask(img):
    """1 where the drawing has ink (clearly darker than the paper)."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    t, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return (g < min(max(t, 90), 170)).astype(np.uint8)


def wall_thickness(ink):
    """Typical thickness in pixels of the thick strokes (walls): twice the 97th percentile of the distance to paper."""
    dt = cv2.distanceTransform(ink, cv2.DIST_L2, 3)
    v = dt[ink > 0]
    return 2.0 * float(np.percentile(v, 97)) if v.size else 0.0


def _blocked(ink, mode, wt, doors, windows):
    if mode == "walls":
        k = max(3, int(round(wt * 0.45)))
        b = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    else:
        b = cv2.dilate(ink, np.ones((2, 2), np.uint8))
    b = b.copy()
    h, w = b.shape
    for x0, y0, x1, y1 in windows:  # a window sits inside the wall: the whole box is wall
        cv2.rectangle(b, (int(x0), int(y0)), (int(round(x1)), int(round(y1))), 1, -1)
    for x0, y0, x1, y1 in doors:    # a door box covers the opening and the swing: its edges close the opening
        cv2.rectangle(b, (int(x0), int(y0)), (int(round(x1)), int(round(y1))), 1, 2)
    b[0, :] = b[-1, :] = 0          # keep the sheet's edge open, so the outside is one region that touches the border
    b[:, 0] = b[:, -1] = 0
    return b


def _try(labels, stats, box, shape, pad):
    h, w = shape
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None, "the room box is too small"
    inside = np.bincount(labels[y0:y1, x0:x1].ravel(), minlength=len(stats))
    inside[0] = 0
    lab = int(inside.argmax())
    if inside[lab] == 0:
        return None, "no open floor area found inside the room box"
    sx, sy, sw, sh, area = stats[lab]
    if sx <= 0 or sy <= 0 or sx + sw >= w or sy + sh >= h:
        return None, "the outline is open to the outside of the drawing"
    if inside[lab] < MIN_COVER * (x1 - x0) * (y1 - y0):
        return None, "the floor area is broken up by lines inside the room"
    px0, py0, px1, py1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
    in_pad = int((labels[py0:py1, px0:px1] == lab).sum())
    if in_pad < MIN_INSIDE * area:
        return None, "the outline is not closed: the space runs on into a neighbouring space"
    return lab, None


def trace_rooms(img, rooms, doors=(), windows=()):
    """rooms, doors, windows: lists of [x0, y0, x1, y1] in image pixels."""
    ink = ink_mask(img)
    h, w = ink.shape
    wt = wall_thickness(ink)
    pad = max(4, int(round(wt)))
    out = [dict(polygon=None, why="not tried") for _ in rooms]
    claimed = {}
    for mode in ("walls", "lines"):
        todo = [i for i, r in enumerate(out) if r["polygon"] is None]
        if not todo or (mode == "walls" and wt < 4):
            continue
        free = (1 - _blocked(ink, mode, wt, doors, windows)).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
        for i in todo:
            lab, why = _try(labels, stats, rooms[i], (h, w), pad)
            if lab is None:
                out[i] = dict(polygon=None, why=why)
                continue
            if (mode, lab) in claimed:  # two detected rooms in one traced space: an open-plan area, not two outlines
                out[i] = dict(polygon=None, why="shares one open space with another detected room")
                continue
            mask = (labels == lab).astype(np.uint8)
            for x0, y0, x1, y1 in doors:  # the swing area of a door belongs to the room it opens into
                a0, b0, a1, b1 = max(0, int(x0) - 3), max(0, int(y0) - 3), min(w, int(round(x1)) + 3), min(h, int(round(y1)) + 3)
                ring = int(mask[b0:b1, a0:a1].sum())
                if ring > 0.25 * (2 * ((a1 - a0) + (b1 - b0)) * 3):
                    mask[max(0, int(y0)):int(round(y1)), max(0, int(x0)):int(round(x1))] = 1
            cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c = max(cs, key=cv2.contourArea)
            if cv2.contourArea(c) < 16:
                out[i] = dict(polygon=None, why="no open floor area found inside the room box")
                continue
            poly = cv2.approxPolyDP(c, max(1.5, 0.004 * cv2.arcLength(c, True)), True).reshape(-1, 2)
            claimed[(mode, lab)] = i
            out[i] = dict(polygon=[[float(x), float(y)] for x, y in poly], mode=mode, closed=True, area_px=float(cv2.contourArea(c)))
    # Rooms with no closed outline (an open doorway, an open-plan space): follow the walls where there are walls and
    # the detector's rectangle where there are none. Marked closed=False so the answer can say so.
    todo = [i for i, r in enumerate(out) if r["polygon"] is None]
    if todo:
        free = (1 - _blocked(ink, "walls" if wt >= 4 else "lines", wt, doors, windows)).astype(np.uint8)
        for i in todo:
            x0, y0, x1, y1 = [int(round(v)) for v in rooms[i]]
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            n, lab_img, stats, _ = cv2.connectedComponentsWithStats(free[y0:y1, x0:x1], connectivity=4)
            if n < 2:
                continue
            lab = 1 + int(stats[1:, cv2.CC_STAT_AREA].argmax())
            if stats[lab, cv2.CC_STAT_AREA] < MIN_COVER * (x1 - x0) * (y1 - y0):
                continue  # mostly lines inside the rectangle: keep the rectangle, do not invent an outline
            cs, _ = cv2.findContours((lab_img == lab).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c = max(cs, key=cv2.contourArea)
            poly = cv2.approxPolyDP(c, max(1.5, 0.004 * cv2.arcLength(c, True)), True).reshape(-1, 2) + np.array([x0, y0])
            out[i] = dict(polygon=[[float(x), float(y)] for x, y in poly], mode="clipped", closed=False, area_px=float(cv2.contourArea(c)),
                          why=out[i].get("why"))
    return out


def trace_from_labels(img, labels, doors=(), windows=()):
    """Like trace_rooms, but each room is given only by the box around its printed name (no room rectangle).

    Without a rectangle there is no size to check a trace against, so the tests are: the region must not run off the
    sheet or cover most of it, and when several names fall into one region they are reported as one shared space."""
    ink = ink_mask(img)
    h, w = ink.shape
    wt = wall_thickness(ink)
    pad = max(3, int(round(wt * 0.5)))
    out = [dict(polygon=None, why="not tried") for _ in labels]
    for mode in ("walls", "lines"):
        todo = [i for i, r in enumerate(out) if r["polygon"] is None]
        if not todo or (mode == "walls" and wt < 4):
            continue
        free = (1 - _blocked(ink, mode, wt, doors, windows)).astype(np.uint8)
        n, lab_img, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
        picked = {}
        for i in todo:
            x0, y0, x1, y1 = [int(round(v)) for v in labels[i]]
            x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
            inside = np.bincount(lab_img[y0:y1, x0:x1].ravel(), minlength=n)
            inside[0] = 0
            lab = int(inside.argmax())
            if inside[lab] == 0:
                out[i] = dict(polygon=None, why="no open floor area found around the printed name")
                continue
            sx, sy, sw, sh, area = stats[lab]
            if sx <= 0 or sy <= 0 or sx + sw >= w or sy + sh >= h:
                out[i] = dict(polygon=None, why="the outline is open to the outside of the drawing")
            elif area > 0.4 * w * h:
                out[i] = dict(polygon=None, why="the outline is not closed: the space covers most of the sheet")
            elif mode == "lines" and area < 6 * max(1, (x1 - x0) * (y1 - y0)) and area < 0.002 * w * h:
                out[i] = dict(polygon=None, why="the floor area is broken up by lines inside the room")
            else:
                picked.setdefault(lab, []).append(i)
        for lab, idx in picked.items():
            mask = (lab_img == lab).astype(np.uint8)
            for x0, y0, x1, y1 in doors:
                a0, b0, a1, b1 = max(0, int(x0) - 3), max(0, int(y0) - 3), min(w, int(round(x1)) + 3), min(h, int(round(y1)) + 3)
                if int(mask[b0:b1, a0:a1].sum()) > 0.25 * (2 * ((a1 - a0) + (b1 - b0)) * 3):
                    mask[max(0, int(y0)):int(round(y1)), max(0, int(x0)):int(round(x1))] = 1
            cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c = max(cs, key=cv2.contourArea)
            poly = cv2.approxPolyDP(c, max(1.5, 0.004 * cv2.arcLength(c, True)), True).reshape(-1, 2)
            for i in idx:
                out[i] = dict(polygon=[[float(x), float(y)] for x, y in poly], mode=mode, area_px=float(cv2.contourArea(c)),
                              shared_with=[j for j in idx if j != i])
    return out
