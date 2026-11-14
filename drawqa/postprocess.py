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


SYMBOLS = ("door", "window", "wc", "sink", "bathtub", "shower", "sauna_stove", "sauna_bench")
FIT_MIN_MEDIAN, FIT_MAX_UNSURE, FIT_MIN_KEPT = 0.65, 0.30, 5


def detector_fit(raw, thresholds, default=0.35):
    """Does the detector know this drawing style? Judged from its own output on the sheet.

    raw = every symbol box above confidence 0.05. "kept" = boxes above their class threshold; "unsure" = boxes between
    0.15 and the threshold. On a familiar style the kept boxes are confident and few candidates are left undecided.
    Measured with detector c26l (2026-11-14): the rule below flags 4 of 241 preview scans and 0 of 65 Swiss PDFs, and
    21 of 24 archive scans from four collections; on the 3 archive scans it lets through, the marked doors and
    windows were checked by eye and are plausible. A warning, not a guarantee: a pass does not prove the count."""
    import statistics
    sym = [d for d in raw if d["cls"] in SYMBOLS]
    kept = [d["conf"] for d in sym if d["conf"] >= thresholds.get(d["cls"], default)]
    # plausibility: windows found but not a single door. Every floor plan has doors, so the detector is missing a symbol
    # it was trained on, and its confidence on what it did find says nothing about that. Seen on a BIM-exported plan PDF
    # (esplanades 4.korrus: 32 windows, 0 doors, median confidence 0.71; the model has 42 doors on that storey). On the
    # 241 preview scans it fires on 1 plan (high_quality_architectural-190: 9 windows, no door question in the key).
    kept_by = {}
    for d in sym:
        if d["conf"] >= thresholds.get(d["cls"], default):
            kept_by[d["cls"]] = kept_by.get(d["cls"], 0) + 1
    unsure = sum(1 for d in sym if 0.15 <= d["conf"] < thresholds.get(d["cls"], default))
    med = round(statistics.median(kept), 2) if kept else None
    share = round(unsure / max(1, len(kept) + unsure), 2)
    out = dict(kept=len(kept), median_conf=med, unsure_share=share)
    if not kept:
        return dict(out, level="none", note="the detector found no door, window or fixture it recognises on this drawing, "
                                            "so it probably does not know this drawing style; zero found does not mean there are none")
    if med < FIT_MIN_MEDIAN or share > FIT_MAX_UNSURE or len(kept) < FIT_MIN_KEPT:
        why = [w for w, bad in ((f"typical confidence only {med:.2f}", med < FIT_MIN_MEDIAN),
                                (f"{round(share * 100)}% of the candidate symbols left undecided", share > FIT_MAX_UNSURE),
                                (f"only {len(kept)} symbol(s) recognised", len(kept) < FIT_MIN_KEPT)) if bad]
        return dict(out, level="weak", note="this drawing style looks unfamiliar to the detector (" + "; ".join(why) + "), so counts may be wrong")
    if kept_by.get("window", 0) >= FIT_MIN_KEPT and not kept_by.get("door"):
        return dict(out, level="weak", missing="door", note=f"the detector found {kept_by['window']} windows but not a single door, which no real floor plan "
                                            "has, so it probably does not recognise this drawing's door symbols; counts may be wrong")
    return dict(out, level="ok", note="")
