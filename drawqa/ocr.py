"""Printed text on an image (scans have no text layer): room labels, room areas, dimensions.

    texts(image_bgr) -> [dict(text, bbox=[x0, y0, x1, y1], conf)]
    room_areas(texts) -> [dict(area_m2, bbox, conf, text)]      "15.3 m2", "14,8m²" ...

Engine: RapidOCR (PP-OCR models on onnxruntime, `pip install rapidocr`); optional, the sheet simply has no texts
without it. Measured on one preview scan with 17 printed room areas: tesseract read 2, RapidOCR read 16 with three
digit errors (35.6 read as 38.6). So a single OCR number is never trusted alone: scale uses the median over rooms.
"""
import re

# the 2 is required: "2.5m" on a plan is a length (a terrace depth), not a room area
AREA = re.compile(r"(?<![\d.,])(\d{1,3})[.,](\d{1,2})\s*m\s*[2²](?![\d])", re.I)
_engine = None


def available():
    try:
        import rapidocr  # noqa: F401
        return True
    except ImportError:
        return False


def texts(img):
    """Small plans are read at twice their size: printed labels on these scans are often under 10 px high."""
    import cv2, logging
    global _engine
    if _engine is None:
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        from rapidocr import RapidOCR
        _engine = RapidOCR()
    up = 2 if max(img.shape[:2]) < 2200 else 1
    big = cv2.resize(img, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC) if up > 1 else img
    r = _engine(big)
    out = []
    if r is None or r.txts is None:
        return out
    for box, t, s in zip(r.boxes, r.txts, r.scores):
        xs, ys = [float(p[0]) / up for p in box], [float(p[1]) / up for p in box]
        if t and t.strip():
            out.append(dict(text=t.strip(), bbox=[round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)], conf=round(float(s), 3)))
    return out


# a number with one decimal and nothing else ("11.0", "8,0", "21:0" with the dot misread): on many plans the small "m2"
# after a room's area is not picked up. Such a number is only a candidate: it counts as an area when an outline
# confirms it (drawqa/convert.py), never on its own.
BARE = re.compile(r"^(\d{1,3})[.,:](\d)$")


def room_areas(texts_):
    """sure=True: the unit was read with the number. sure=False: a bare one-decimal number, a candidate only."""
    out = []
    for t in texts_:
        hit = False
        for m in AREA.finditer(t["text"]):
            a = float(m.group(1) + "." + m.group(2))
            if 0.5 <= a <= 400:
                out.append(dict(area_m2=a, bbox=t["bbox"], conf=t["conf"], text=t["text"], sure=True))
                hit = True
        m = None if hit else BARE.match(t["text"])
        if m and 1.0 <= float(m.group(1) + "." + m.group(2)) <= 200:
            out.append(dict(area_m2=float(m.group(1) + "." + m.group(2)), bbox=t["bbox"], conf=t["conf"], text=t["text"], sure=False))
    return out
