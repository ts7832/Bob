"""Swiss plans: exact element boxes in PNG pixel coordinates, computed from the DXF.

The PNG is a render of the PDF page; DXF metres map to PDF points by a scale (1:100 -> 28.3465 pt/m, 1:200 -> half)
and an offset, recovered by matching room-number texts between the DXF and the PDF.
    boxes(dxf_path) -> (png_path, width, height, [(cls, x0, y0, x1, y1)], info)
"""
import os, re, statistics, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pymupdf
from PIL import Image
from drawqa import swiss

PT_PER_M_1_100 = 72 / 25.4 * 10  # 1 m drawn at 1:100 = 10 mm
CLS = {"wc": "wc", "washbasin": "sink", "sink": "sink", "bathtub": "bathtub", "shower": "shower"}


def transform(dxf_path):
    """Returns (pt_per_m, tx, ty, n_matched): pdf_x = s*x + tx ; pdf_y_from_top = ty - s*y."""
    sheet = swiss.load(dxf_path)
    page = pymupdf.open(dxf_path[:-4] + ".pdf")[0]
    words = {}
    for x0, y0, x1, y1, w, *_ in page.get_text("words"):
        if re.fullmatch(r"[A-Z]?\d+\.\d+", w):
            words.setdefault(w, (x0, y1))  # left / baseline-ish corner of the word, y from the top of the page
    pairs = [(r["text_pos"], words[r["number"]]) for r in sheet["rooms"] if r.get("number") in words]
    if len(pairs) < 3:
        return None
    # scale from pairwise distances, snapped to a standard drawing scale
    (a0, b0), (a1, b1) = pairs[0], max(pairs, key=lambda p: abs(p[0][0] - pairs[0][0][0]) + abs(p[0][1] - pairs[0][0][1]))
    raw = (((b1[0] - b0[0]) ** 2 + (b1[1] - b0[1]) ** 2) ** 0.5) / max(1e-9, ((a1[0] - a0[0]) ** 2 + (a1[1] - a0[1]) ** 2) ** 0.5)
    s = min((PT_PER_M_1_100 * 100 / d for d in (20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500)), key=lambda v: abs(v - raw))
    tx = statistics.median(b[0] - s * a[0] for a, b in pairs)
    ty = statistics.median(b[1] + s * a[1] for a, b in pairs)
    # Text anchors differ between DXF and PDF, which leaves a constant offset of several points. Remove it by
    # matching room outlines (present in both files with identical size) and taking the median shift.
    rects = [(d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1) for d in page.get_drawings()]
    dx, dy = [], []
    for r in sheet["rooms"]:
        P = r.get("polygon")
        if not P:
            continue
        X = sorted([s * min(p[0] for p in P) + tx, s * max(p[0] for p in P) + tx])
        Y = sorted([ty - s * max(p[1] for p in P), ty - s * min(p[1] for p in P)])
        w, h = X[1] - X[0], Y[1] - Y[0]
        cand = [q for q in rects if abs((q[2] - q[0]) - w) + abs((q[3] - q[1]) - h) < 0.6 and abs(q[0] - X[0]) < 20 and abs(q[1] - Y[0]) < 20]
        if cand:
            q = min(cand, key=lambda q: abs(q[0] - X[0]) + abs(q[1] - Y[0]))
            dx.append(q[0] - X[0])
            dy.append(q[1] - Y[0])
    refined = len(dx) >= 3
    if refined:
        tx += statistics.median(dx)
        ty += statistics.median(dy)
    return s, tx, ty, (len(pairs) if refined else 0), sheet, page.rect.width, page.rect.height


def boxes(dxf_path):
    t = transform(dxf_path)
    if t is None:
        return None
    s, tx, ty, n, sheet, pw, ph = t
    if n == 0:
        return None  # could not register this file precisely; skip it rather than write shifted labels
    png = dxf_path[:-4] + ".png"
    w, h = Image.open(png).size
    kx, ky = w / pw, h / ph
    out = []
    for group in ("openings", "fixtures"):
        for e in sheet[group]:
            cls = e["cls"] if group == "openings" else CLS.get(e["cls"])
            if cls is None:
                continue
            x0, y0, x1, y1 = e["bbox"]
            X = sorted([(s * x0 + tx) * kx, (s * x1 + tx) * kx])
            Y = sorted([(ty - s * y0) * ky, (ty - s * y1) * ky])
            out.append((cls, X[0], Y[0], X[1], Y[1]))
    return png, w, h, out, dict(pt_per_m=round(s, 3), matched=n)


if __name__ == "__main__":  # overlay check: python tools/swiss_labels.py <dxf> <out.png>
    import cv2
    png, w, h, bx, info = boxes(sys.argv[1])
    img = cv2.imread(png)
    col = {"door": (0, 0, 255), "window": (255, 128, 0), "wc": (0, 160, 0), "sink": (200, 0, 200), "bathtub": (0, 100, 255), "shower": (0, 200, 200)}
    for c, x0, y0, x1, y1 in bx:
        cv2.rectangle(img, (int(x0), int(y0)), (int(x1), int(y1)), col[c], 2)
    cv2.imwrite(sys.argv[2], img)
    print(info, len(bx), "boxes", w, h)
