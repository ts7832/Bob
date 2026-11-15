"""Complete the repeats: symbols the detector found with confidence are used as templates to find the identical symbols it
missed on the same sheet (on a US dormitory plan it found every second window of a regular facade).

    complete(img, dets, cls="window") -> new detections (same format as the detector's: cls, bbox, conf)

A template match alone is not enough: in many CAD styles a window is a wall band with a few extra lines, and plain walls
match the template almost as well (0.99). So a match is kept only if its pattern ALONG the wall matches the template's
(window frames and mullions make a profile a plain wall does not have): correlation >= 0.95 and a similar spread. On
the dormitory sheet this kept all 16 hand-checked missed windows plus one corridor window and none of 43 wall pieces.
"""
import numpy as np

MIN_CONF = 0.5      # detections used as templates
MATCH = 0.8         # template-match score to consider a place at all
ALONG_CORR = 0.95   # pattern along the wall must match the template's this well
MAX_TEMPLATES = 24  # at most this many distinct looks per sheet (one matchTemplate each, ~1 s on a 10k px sheet)


def _along(gray, b, n=64):
    x0, y0, x1, y1 = [int(round(v)) for v in b]
    p = gray[max(0, y0):y1, max(0, x0):x1].astype(np.float32)
    if p.size == 0:
        return None
    a = p.mean(axis=0) if (x1 - x0) >= (y1 - y0) else p.mean(axis=1)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)


def _overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / max(1.0, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))


def complete(img, dets, cls="window"):
    import cv2
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sure = sorted((d for d in dets if d["cls"] == cls and d["conf"] >= MIN_CONF), key=lambda d: -d["conf"])
    if len(sure) < 3:  # too few sure examples to call anything a repeat
        return []
    # One template per distinct look: the surest window not yet matched by an earlier template. Picking by size group
    # missed the very type the detector under-finds (it had only one sure example of it).
    covered = set()
    taken = [d["bbox"] for d in dets]
    new = []
    used = 0
    for i, t in enumerate(sure):
        if i in covered or used >= MAX_TEMPLATES:
            continue
        x0, y0, x1, y1 = [int(round(v)) for v in t["bbox"]]
        tm = gray[max(0, y0):y1, max(0, x0):x1]
        ta = _along(gray, t["bbox"])
        if tm.size == 0 or min(tm.shape) < 4 or tm.std() < 5 or ta is None or ta.std() < 1:
            continue
        used += 1
        r = cv2.matchTemplate(gray, tm, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(r >= MATCH)
        order = np.argsort(-r[ys, xs])
        h, w = tm.shape
        hits = [[float(xs[k]), float(ys[k]), float(xs[k] + w), float(ys[k] + h)] for k in order[:20000]]
        for j, s in enumerate(sure):  # sure windows this template already explains need no template of their own
            if j not in covered and any(_overlap(s["bbox"], b) > 0.5 for b in hits[:2000]):
                covered.add(j)
        for b in hits[:5000]:
            if any(_overlap(b, o) > 0.3 for o in taken):
                continue
            a = _along(gray, b)
            if a is None or a.std() < 1:
                continue
            c = max(float(np.corrcoef(a, ta)[0, 1]), float(np.corrcoef(a[::-1], ta)[0, 1]))
            if not c >= ALONG_CORR or abs(a.std() - ta.std()) > max(4.0, 0.2 * ta.std()):
                continue
            nd = dict(cls=cls, bbox=[round(v, 1) for v in b], conf=round(min(t["conf"], 0.6) * c, 3), repeat=True,
                      found_by=f"repeat of a detected {cls}")
            new.append(nd)
            taken.append(nd["bbox"])
    return new
