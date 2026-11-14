"""Turning raw detector boxes into countable elements.

merge_windows: a long window with mullions is often detected as several panes next to each other. Boxes of one
class that lie in a row along the same wall and (almost) touch are one element. The allowed gap is a multiple of the
wall thickness (the box's short side) and is tuned on held-out plans together with the confidence threshold.
"""


def _same(a, b, gap):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    iw, ih = min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0)
    area_a, area_b = (ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0)
    if iw > 0 and ih > 0 and iw * ih >= 0.5 * min(area_a, area_b):
        return True  # one box sits mostly inside the other
    if gap is None:
        return False
    ta, tb = min(ax1 - ax0, ay1 - ay0), min(bx1 - bx0, by1 - by0)
    t = (ta + tb) / 2
    # in a row horizontally: overlap across (y), small gap along (x) -- and the same test vertically
    for (a0, a1, b0, b1), (c0, c1, d0, d1) in (((ax0, ax1, bx0, bx1), (ay0, ay1, by0, by1)), ((ay0, ay1, by0, by1), (ax0, ax1, bx0, bx1))):
        across = min(c1, d1) - max(c0, d0)
        along_gap = max(a0, b0) - min(a1, b1)
        thick = min(c1 - c0, d1 - d0)
        if across >= 0.5 * thick and along_gap <= gap * t and max(c1 - c0, d1 - d0) <= 2.5 * thick:
            return True
    return False


def merge_row(boxes, gap):
    """boxes = [(bbox, conf)] of ONE class -> merged [(bbox, conf)]. gap=None merges only nested boxes."""
    n = len(boxes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            if _same(boxes[i][0], boxes[j][0], gap):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(boxes[i])
    out = []
    for g in groups.values():
        out.append(([min(b[0][0] for b in g), min(b[0][1] for b in g), max(b[0][2] for b in g), max(b[0][3] for b in g)], max(b[1] for b in g)))
    return out


def apply(dets, thresholds, rules=None, default_thr=0.35):
    """dets = [dict(cls, bbox, conf)] raw -> thresholded and merged list in the same form.
    rules = {"window": {"gap": 1.0}, ...} (from the tuned thresholds file); classes without a rule are only thresholded."""
    rules = rules or {}
    out = []
    by_cls = {}
    for d in dets:
        if d["conf"] >= thresholds.get(d["cls"], default_thr):
            by_cls.setdefault(d["cls"], []).append(d)
    for c, ds in by_cls.items():
        if c in rules:
            for bb, conf in merge_row([(d["bbox"], d["conf"]) for d in ds], rules[c].get("gap")):
                out.append(dict(cls=c, bbox=[round(v, 1) for v in bb], conf=conf))
        else:
            out += ds
    return out
