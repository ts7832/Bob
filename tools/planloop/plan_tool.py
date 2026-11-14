"""Tools for a model that rebuilds a floor plan from its image in DrawQA's sheet format, and checks itself by overlay.

  python plan_tool.py grid   <image> <out.png> [x0 y0 x1 y1] [--step N]
      the image (or a region) with a labelled pixel grid, so positions can be read off exactly
  python plan_tool.py render <image> <model.json> <out.png> [x0 y0 x1 y1]
      the model drawn over the image (or a region) + a printed summary (counts per class, room areas)
  python plan_tool.py check  <image> <model.json> <outdir> [--n N]
      the overlay cut into N x N overlapping zoomed tiles WITH the pixel grid (default N=2): check_r{row}c{col}.png

model.json: pixel coordinates of the ORIGINAL image (x right, y down). Classes are DrawQA's own:
{
  "m_per_px": null,                                   # metres per pixel, only if the sheet gives a scale (scale bar,
  "scale_source": "",                                 #   or a printed room area you can match to its outline)
  "floors":   [{"id": "F1", "bbox": [x0, y0, x1, y1]}],              # each separate floor plan drawn on the sheet
  "rooms":    [{"id": "r1", "name": "<printed name, as written>", "type": "<room type>", "polygon": [[x, y], ...]}],
  "doors":    [{"id": "d1", "bbox": [x0, y0, x1, y1]}],              # a doorway WITH a door leaf (swing or sliding)
  "windows":  [{"id": "w1", "bbox": [x0, y0, x1, y1]}],              # one per window opening in an outer wall
  "fixtures": [{"id": "f1", "type": "<fixture type>", "bbox": [x0, y0, x1, y1]}]
}
room types:    bedroom, living_room, kitchen, dining, bathroom, wc_room, sauna_room, utility, storage, dressing_room,
               entry, balcony, terrace, garage, room (a room whose kind is unclear), other_room
fixture types: wc (toilet bowl), sink (any sink or wash basin; a double-bowl sink is one), shower, bathtub,
               sauna_stove, sauna_bench
"""
import json, sys
from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None  # sheets of 14000 px


def _open(path, region_args):
    """Large sheets: load greyscale, crop the region FIRST, then make the crop RGB (a 14000 px sheet is 140 MB in L, not 420 MB)."""
    im = Image.open(path)
    W, H = im.size
    region = _region_wh(W, H, region_args)
    crop = im.convert("L").crop(region).convert("RGB")
    return crop, region, (W, H)


def _region_wh(W, H, args):
    if len(args) >= 4:
        x0, y0, x1, y1 = map(int, args[:4])
        return max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    return 0, 0, W, H

MAX_VIEW = 1800
ROOM_COLOURS = {"bedroom": (59, 130, 246), "living_room": (16, 185, 129), "kitchen": (245, 158, 11), "dining": (234, 179, 8),
                "bathroom": (6, 182, 212), "wc_room": (139, 92, 246), "sauna_room": (239, 68, 68), "utility": (132, 204, 22),
                "storage": (120, 113, 108), "dressing_room": (168, 162, 158), "entry": (236, 72, 153), "balcony": (34, 197, 94),
                "terrace": (21, 128, 61), "garage": (100, 116, 139), "room": (148, 163, 184), "other_room": (148, 163, 184)}


def _font(size):
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _region(img, args):
    if len(args) >= 4:
        x0, y0, x1, y1 = map(int, args[:4])
        return max(0, x0), max(0, y0), min(img.width, x1), min(img.height, y1)
    return 0, 0, img.width, img.height


def _save(im, out, region, upscale=False):
    x0, y0, x1, y1 = region
    k = MAX_VIEW / max(im.size)
    k = min(k, 3.0) if upscale else min(1.0, k)
    if k != 1:
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    im.save(out)
    print(f"wrote {out}: region x {x0}-{x1}, y {y0}-{y1} of the original, shown at {k:.2f}x")


def _grid(img, region, step=None):
    ox, oy = region[0], region[1]
    span = max(region[2] - region[0], region[3] - region[1])
    step = step or max(10, int(round(span / 12 / 10.0)) * 10)
    d = ImageDraw.Draw(img, "RGBA")
    f = _font(max(9, span // 70))
    lw = max(1, span // 1200)
    for x in range(((region[0] + step - 1) // step) * step, region[2] + 1, step):
        d.line([(x - ox, 0), (x - ox, region[3] - oy)], fill=(255, 0, 0, 70), width=lw)
        d.text((x - ox + 2, 2), str(x), fill=(200, 0, 0, 255), font=f)
    for y in range(((region[1] + step - 1) // step) * step, region[3] + 1, step):
        d.line([(0, y - oy), (region[2] - ox, y - oy)], fill=(255, 0, 0, 70), width=lw)
        d.text((2, y - oy + 2), str(y), fill=(200, 0, 0, 255), font=f)


def grid(path, out, args):
    step = int(args[args.index("--step") + 1]) if "--step" in args else None
    args = [a for i, a in enumerate(args) if a != "--step" and (i == 0 or args[i - 1] != "--step")]
    img, region, _ = _open(path, args)
    _grid(img, region, step)
    _save(img, out, region, upscale=len(args) >= 4)


def poly_area(p):
    return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))) / 2


def draw_model(img, m, span, off=(0, 0)):
    ox, oy = off
    B = lambda b: [b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy]
    d = ImageDraw.Draw(img, "RGBA")
    f = _font(max(9, span // 70))
    lw = max(1, span // 500)
    k = m.get("m_per_px")
    for fl in m.get("floors", []) if isinstance(m.get("floors"), list) else []:
        d.rectangle(B(fl["bbox"]), outline=(0, 0, 0, 200), width=lw)
        d.text((fl["bbox"][0] - ox + 3, fl["bbox"][1] - oy + 3), fl.get("id", "floor"), fill=(0, 0, 0, 255), font=f)
    for r in m.get("rooms", []):
        c = ROOM_COLOURS.get(r.get("type"), ROOM_COLOURS["other_room"])
        pts0 = [tuple(p) for p in r.get("polygon", [])]
        pts = [(x - ox, y - oy) for x, y in pts0]
        if len(pts) >= 3:
            d.polygon(pts, fill=c + (55,), outline=c + (255,))
            cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            label = f"{r.get('id', '')} {r.get('name') or ''} [{r.get('type', '')}]" + (f" {poly_area(pts0) * k * k:.1f}m²" if k else "")
            d.text((cx, cy), label, fill=(0, 0, 0, 255), font=f, anchor="mm")
    for kind, colour in (("doors", (37, 99, 235)), ("windows", (13, 148, 136)), ("fixtures", (147, 51, 234))):
        for i, e in enumerate(m.get(kind, [])):
            b = B(e["bbox"])
            d.rectangle(b, outline=colour + (255,), width=lw)
            tag = (e.get("id") or f"{kind[0]}{i + 1}") + (f" {e['type']}" if kind == "fixtures" else "")
            d.text((b[0], b[1] - 1), tag, fill=colour + (255,), font=f, anchor="ld")
    return img


def summary(m):
    cnt = lambda key, field: {t: sum(1 for e in m.get(key, []) if e.get(field) == t) for t in sorted({e.get(field) for e in m.get(key, [])}, key=str)}
    k = m.get("m_per_px")
    return dict(floors=len(m.get("floors", [])) if isinstance(m.get("floors"), list) else m.get("floors"),
                doors=len(m.get("doors", [])), windows=len(m.get("windows", [])), fixtures=cnt("fixtures", "type"), rooms=cnt("rooms", "type"),
                room_areas_m2=None if not k else {r.get("id"): round(poly_area(r["polygon"]) * k * k, 2) for r in m.get("rooms", []) if len(r.get("polygon", [])) >= 3})


def render(path, model_path, out, args):
    img, region, _ = _open(path, args)
    m = json.load(open(model_path))
    draw_model(img, m, max(region[2] - region[0], region[3] - region[1]), off=region[:2])
    _save(img, out, region, upscale=len(args) >= 4)
    print(json.dumps(summary(m)))


def check(path, model_path, outdir, args):
    import os
    n = int(args[args.index("--n") + 1]) if "--n" in args else 2
    os.makedirs(outdir, exist_ok=True)
    base = Image.open(path).convert("L")
    m = json.load(open(model_path))
    W, H = base.size
    tw, th = W / n, H / n
    for r in range(n):
        for c in range(n):
            x0, y0 = int(max(0, c * tw - 0.15 * tw)), int(max(0, r * th - 0.15 * th))
            x1, y1 = int(min(W, (c + 1) * tw + 0.15 * tw)), int(min(H, (r + 1) * th + 0.15 * th))
            img = base.crop((x0, y0, x1, y1)).convert("RGB")
            span = max(x1 - x0, y1 - y0)
            draw_model(img, m, span, off=(x0, y0))
            _grid(img, (x0, y0, x1, y1))
            _save(img, os.path.join(outdir, f"check_r{r}c{c}.png"), (x0, y0, x1, y1), upscale=True)
    print(json.dumps(summary(m)))


def _inside(x, y, poly):
    c = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def lint(model_path, args):
    """Problems a reader should resolve: overlapping rooms, rooms holding several printed words (maybe two rooms),
    printed words inside no room (maybe a missing room), rooms without a name. Printed words come from OCR (--ocr file):
    upside-down labels can come out reversed ("HW" = MH, "HO" = OH) and some are misread; treat them as hints."""
    import re
    m = json.load(open(model_path))
    rooms = [r for r in m.get("rooms", []) if len(r.get("polygon") or []) >= 3]
    out = []
    try:
        from shapely.geometry import Polygon
        P = {r.get("id"): Polygon(r["polygon"]).buffer(0) for r in rooms}
        ids = list(P)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = P[ids[i]], P[ids[j]]
                inter = a.intersection(b).area
                if inter > 0.03 * min(a.area, b.area):
                    out.append(f"OVERLAP {ids[i]} and {ids[j]}: {inter / min(a.area, b.area):.0%} of the smaller one")
    except Exception as e:
        out.append(f"(overlap check unavailable: {e})")
    if "--ocr" in args:
        words = [t for t in json.load(open(args[args.index("--ocr") + 1])) if re.search(r"[A-Za-zÄÖÅäöå]", t["text"]) and len(t["text"]) <= 25]
        fl = [f["bbox"] for f in m.get("floors", [])] if isinstance(m.get("floors"), list) else []
        held = {r.get("id"): [] for r in rooms}
        for t in words:
            cx, cy = (t["bbox"][0] + t["bbox"][2]) / 2, (t["bbox"][1] + t["bbox"][3]) / 2
            hits = [r.get("id") for r in rooms if _inside(cx, cy, r["polygon"])]
            for h in hits:
                held[h].append(t["text"])
            if not hits and (not fl or any(f[0] <= cx <= f[2] and f[1] <= cy <= f[3] for f in fl)):
                out.append(f"WORD IN NO ROOM '{t['text']}' at ({cx:.0f}, {cy:.0f}): a missing room, or not a room name")
        for r in rooms:
            ws = sorted(set(held[r.get("id")]))
            if len(ws) >= 2:
                out.append(f"SEVERAL WORDS in {r.get('id')} ({r.get('name')}): {ws}: two rooms in one outline? (or a name plus an area or note)")
    doors = [d["bbox"] for d in m.get("doors", []) if len(d.get("bbox") or []) == 4]
    for r in rooms:
        if not r.get("name"):
            xs, ys = [p[0] for p in r["polygon"]], [p[1] for p in r["polygon"]]
            pad = 0.05 * max(max(xs) - min(xs), max(ys) - min(ys), 40)
            touches = any(b[0] <= max(xs) + pad and b[2] >= min(xs) - pad and b[1] <= max(ys) + pad and b[3] >= min(ys) - pad for b in doors)
            if touches:
                out.append(f"NO NAME on {r.get('id')} ({r.get('type')}): fine for an unnamed hall or passage, otherwise read its label")
            else:
                out.append(f"NO NAME, NO DOOR on {r.get('id')} ({r.get('type')}): part of a neighbouring room (merge), outside the dwelling "
                           f"(delete), or a real room reached through an open passage (keep): look")
    print("\n".join(out) if out else "no problems found")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "lint":
        lint(sys.argv[2], sys.argv[3:])
        sys.exit(0)
    if cmd == "grid":
        grid(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif cmd == "render":
        render(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
    elif cmd == "check":
        check(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
