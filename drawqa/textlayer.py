"""Text on a large drawing image, read at full resolution: the text layer that vector PDFs have and images do not.

Whole-image OCR shrinks a big sheet before reading it. On a 10200 x 6600 px piping plan (us_aw_078-24-28 p096) it read
227 text boxes and 24 equipment tags; reading 2000 px tiles at native resolution read 493 boxes and 55 tags (all of
CV-1..5, FP-1..5, FSCV-1..7, FSI-1..5, FSR-1..2, PIT 1..6, and PS-1..24 but PS-2) in 48 s on the Mac.

    read(path, img=None) -> [dict(text, raw, bbox=[x0, y0, x1, y1] in image px, conf)]   (cached on disk per file)

Tiles overlap by OVERLAP px. A box that touches a tile's inner edge is dropped when it is narrower than the overlap
(the neighbouring tile holds that word whole); the same word read in two tiles is kept once. `text` has common OCR
slips in drawing notation fixed (inch marks read as * or °); `raw` is what OCR returned.
"""
import hashlib, json, os, re

TILE, OVERLAP = 2000, 300
CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
BIG = 5000  # images with a longer side than this are read in tiles (smaller ones whole, as before)


def _key(path):
    st = os.stat(path)
    return os.path.join(CACHE, "textlayer_" + hashlib.sha1(
        f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{TILE}|{OVERLAP}".encode()).hexdigest() + ".json")


def normalize(t):
    """Drawing notation that OCR gets wrong in a predictable way: 16* / 16° / 16'' -> 16" (inches)."""
    t = re.sub(r"(?<=\d)\s?(?:\*|°|''|”|“)", '"', t)
    return t.strip()


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0


def tiles(W, H, size=TILE, overlap=OVERLAP):
    step = size - overlap
    for y in range(0, max(1, H - overlap), step):
        for x in range(0, max(1, W - overlap), step):
            yield x, y, min(x + size, W), min(y + size, H)


def read(path, img=None):
    f = _key(path)
    if os.path.exists(f):
        return json.load(open(f))
    import cv2
    from . import remote
    if img is None:
        img = cv2.imread(path)
    out = remote.try_call("textlayer", img) if remote.enabled() else None  # the whole tiled read in one call on Modal
    if out is None:
        out = read_image(img)
    os.makedirs(CACHE, exist_ok=True)
    json.dump(out, open(f, "w"))
    return out


def read_image(img):
    """The text on an image, in native-resolution tiles when it is big (no cache: read() keeps one)."""
    from . import ocr
    H, W = img.shape[:2]
    if max(H, W) <= BIG:
        out = [dict(t, raw=t["text"], text=normalize(t["text"])) for t in ocr.texts(img)]
    else:
        found = []
        for x0, y0, x1, y1 in tiles(W, H):
            tile = img[y0:y1, x0:x1]
            if tile.size == 0 or tile.mean() > 252:  # blank paper
                continue
            for t in ocr.texts(tile):
                bx = [t["bbox"][0] + x0, t["bbox"][1] + y0, t["bbox"][2] + x0, t["bbox"][3] + y0]
                w = bx[2] - bx[0]
                cut = ((x0 > 0 and t["bbox"][0] < 4) or (y0 > 0 and t["bbox"][1] < 4)
                       or (x1 < W and t["bbox"][2] > (x1 - x0) - 4) or (y1 < H and t["bbox"][3] > (y1 - y0) - 4))
                if cut and w < OVERLAP:  # cut at the tile edge; the neighbouring tile has it whole
                    continue
                found.append(dict(text=normalize(t["text"]), raw=t["text"], bbox=[round(v, 1) for v in bx], conf=t["conf"]))
        out = []
        same = lambda a, b: a == b or (len(a) > 3 and len(b) > 3 and (a in b or b in a))
        for t in sorted(found, key=lambda t: -t["conf"]):  # the same word read in two overlapping tiles: keep it once
            # (only the same text: tightly stacked labels like PIT 3 / PIT 4 / PIT 5 overlap each other's boxes)
            if not any(_iou(t["bbox"], o["bbox"]) > 0.5 and same(t["text"], o["text"]) for o in out):
                out.append(t)
        out.sort(key=lambda t: (round(t["bbox"][1] / 20), t["bbox"][0]))
    return out


if __name__ == "__main__":  # python -m drawqa.textlayer image.png out.json
    import sys, time
    t0 = time.time()
    T = read(sys.argv[1])
    if len(sys.argv) > 2:
        json.dump(T, open(sys.argv[2], "w"), indent=0)
    print(f"{os.path.basename(sys.argv[1])}: {len(T)} text boxes in {time.time() - t0:.0f}s")
