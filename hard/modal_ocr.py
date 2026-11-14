"""Read every piece of printed text on a large drawing image, with its position, on Modal.

    modal run hard/modal_ocr.py --src ~/BOB/STOA-extra_examples     # -> hard/out/<name>.texts.json

The sheet is cut into overlapping tiles so small lettering keeps its size; tile results are merged and duplicates
from the overlaps removed. Output: [{"text", "conf", "box": [x0, y0, x1, y1]}] in pixels of the original image.
"""
import json, os, pathlib

import modal

APP = modal.App("bob-hard-ocr")
image = (modal.Image.debian_slim(python_version="3.11").apt_install("libgl1", "libglib2.0-0")
         .pip_install("rapidocr-onnxruntime==1.4.4", "opencv-python-headless", "numpy<2", "pillow"))
TILE, OVERLAP = 1600, 240


@APP.function(image=image, cpu=8, memory=16384, timeout=1800)
def read_tiles(png: bytes, boxes: list, scale: float = 1.0):
    """OCR a batch of tiles of one image. boxes: [(x0, y0, x1, y1)] in original pixels."""
    import io
    import numpy as np
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(io.BytesIO(png)).convert("RGB")
    ocr = RapidOCR()
    out = []
    for x0, y0, x1, y1 in boxes:
        tile = im.crop((x0, y0, x1, y1))
        if scale != 1.0:
            tile = tile.resize((int(tile.width * scale), int(tile.height * scale)), Image.LANCZOS)
        res, _ = ocr(np.asarray(tile)[:, :, ::-1])
        for quad, text, conf in res or []:
            xs, ys = [p[0] / scale + x0 for p in quad], [p[1] / scale + y0 for p in quad]
            horizontal = abs(quad[1][0] - quad[0][0]) >= abs(quad[1][1] - quad[0][1])
            out.append(dict(text=text, conf=round(float(conf), 3), box=[round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))],
                            vertical=not horizontal))
    return out


def _merge(items):
    """Drop duplicates from overlapping tiles: same text, boxes overlapping by more than half; keep the longer / surer."""
    items = sorted(items, key=lambda t: (-len(t["text"]), -t["conf"]))
    kept = []
    for t in items:
        x0, y0, x1, y1 = t["box"]
        dup = False
        for k in kept:
            a0, b0, a1, b1 = k["box"]
            iw, ih = min(x1, a1) - max(x0, a0), min(y1, b1) - max(y0, b0)
            if iw > 0 and ih > 0 and iw * ih > 0.5 * (x1 - x0) * (y1 - y0):
                dup = True
                break
        if not dup:
            kept.append(t)
    return sorted(kept, key=lambda t: (t["box"][1], t["box"][0]))


@APP.local_entrypoint()
def main(src: str, scale: float = 1.0):
    import struct
    out_dir = pathlib.Path(__file__).resolve().parent / "out"
    out_dir.mkdir(exist_ok=True)
    files = sorted(pathlib.Path(os.path.expanduser(src)).glob("*.png"))
    jobs = []
    for f in files:
        w, h = struct.unpack(">II", f.read_bytes()[16:24])   # PNG header
        step = TILE - OVERLAP
        boxes = [(x, y, min(x + TILE, w), min(y + TILE, h)) for y in range(0, max(1, h - OVERLAP), step) for x in range(0, max(1, w - OVERLAP), step)]
        png = f.read_bytes()
        chunks = [boxes[i::6] for i in range(6)]
        jobs.append((f, w, h, [read_tiles.spawn(png, c, scale) for c in chunks if c]))
    for f, w, h, calls in jobs:
        texts = _merge([t for c in calls for t in c.get()])
        json.dump(dict(file=f.name, width=w, height=h, texts=texts), open(out_dir / (f.stem + ".texts.json"), "w"), ensure_ascii=False)
        print(f"{f.name}: {w}x{h}, {len(texts)} text items")
