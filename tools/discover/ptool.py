"""Precision tools for reading a drawing: every item's geometry comes from the pixels, not from eyeballing.
All coordinates are full-resolution sheet pixels. Run: python ptool.py <command> ...
Every command takes --ink N: pixels darker than N (0-255) count as drawing (default 170; use 200-215 for grey
linework such as furniture drawn in light grey). On a very large sheet give find_like an --in region: matching the
whole sheet at once is slow and uses a lot of memory.

  find_like <image> x0 y0 x1 y1 <out.json> [--in X0 Y0 X1 Y1] [--min 0.7] [--rot] [--scales a b c] [--sheet out.png]
      One example of a symbol (its box) -> every look-alike on the sheet (or inside --in), by normalised template
      matching; --rot also tries 90/180/270 degrees and mirror images; --scales 0.8 1.25 1.5 also tries those sizes
      of the example (a symbol drawn at several sizes). Writes hits {bbox, score, rot} sorted by score
      and, with --sheet, a contact sheet of every hit at full resolution, numbered, for you to accept or reject.
  auto <image> <classes.json> <outdir> [--min 0.72] [--review 0.55] [--scales 0.85 1.2]
      Every box class of a survey at once: find_like from each example with rotations, mirrors and sizes; hits
      >= --min accepted, lower ones down to --review put on a review sheet. Per class: <class>.json, <class>_ok.png,
      <class>_review.png (numbered contact sheets). Large sheets are searched in tiles.
  lint_layer <discovered.json> [<image>]
      Flags probable noise and mistakes in a result, in any class: ragged outlines, items far bigger or smaller than the
      rest of their class, near-duplicates, items outside the image. Resolve every flag (fix or drop) before finishing.
  snap_box <image> x0 y0 x1 y1 [--pad 6]
      A rough box -> the tight box of the ink inside it (+pad px). Walls and long straight lines are removed first,
      so a WC against a wall snaps to the WC. Works on clean drawings; in a dense scan where fixtures touch each other
      it cannot tell them apart: there, read the edges on a `grid` view instead.
  snap_line <image> x0 y0 x1 y1 [--search 60] [--mpp M]
      A rough straight segment -> the drawn line or parallel line pair nearest to it, same direction. Prints the exact
      centreline, the width between the pair (px; inches and mm with --mpp), or "single line".
  fill <image> x y [--close 0] [--bar x0 y0 x1 y1 ...] [--max_frac 0.25]
      The enclosed area of paper around point (x, y) -> its outline polygon and area in px. --close N first bridges gaps
      up to N px in the linework (broken lines); --bar draws a temporary line, e.g. across a door opening (repeatable). Refuses areas larger than max_frac of the sheet (leaked).
  trace <image> x y x2 y2 [--max 6000] [--mpp M] [--out f.json]
      Follow a drawn pipe, duct or line from (x, y), heading towards (x2, y2): at every step it re-centres between
      the two drawn edges (or on the single line), turns with the drawing through bends and elbows, and coasts
      straight through valves, flanges and cross marks of the same run. Stops at the run's end, a tee or a big change
      of width. Prints a dense centreline (curves kept), the width along it, and why it stopped.
  crops <image> <items.json> <out.png> [--pad 40] [--cols 6] [--max 60] [--class NAME]
      items.json: a list of {"bbox"|"polyline"|"polygon"|"point", "label"?}, a find_like output, or a discovered.json
      (--class NAME for one class).
      Draws each item on a full-resolution crop around it, numbered, in a grid: the sheet for checking that every
      item sits on its object.
"""
import json, sys
import cv2
import numpy as np

Image = None


def load(path):
    g = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if g is None:
        sys.exit(f"cannot read {path}")
    return g


def opt(a, name, n=1, cast=float, default=None):
    if name not in a:
        return default
    i = a.index(name)
    v = [cast(x) for x in a[i + 1:i + 1 + n]]
    return v if n > 1 else v[0]


def _variants(tpl, rot, scales):
    v = [(0, False, tpl)]
    for sc in scales or []:
        if abs(sc - 1) > 0.01:
            v.append((0, False, cv2.resize(tpl, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_LINEAR)))
    if rot:
        for k in (1, 2, 3):
            v.append((90 * k, False, np.rot90(tpl, k)))
        for k in (0, 1, 2, 3):
            v.append((90 * k, True, np.rot90(np.fliplr(tpl), k)))
    return v


def _match_region(hay, variants, lo, ox, oy):
    th = max(t.shape[0] for _, _, t in variants); tw = max(t.shape[1] for _, _, t in variants)
    f = 2 if max(hay.shape) > 2000 and min(th, tw) >= 24 else 1  # coarse search, refined at full resolution
    hay_s = cv2.resize(hay, None, fx=1 / f, fy=1 / f, interpolation=cv2.INTER_AREA) if f > 1 else hay
    hits = []
    for rot, flip, t in variants:
        t_s = cv2.resize(t, None, fx=1 / f, fy=1 / f, interpolation=cv2.INTER_AREA) if f > 1 else t
        if t_s.shape[0] >= hay_s.shape[0] or t_s.shape[1] >= hay_s.shape[1] or t_s.std() < 1:
            continue
        r = cv2.matchTemplate(hay_s, t_s, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(r >= lo - (0.08 if f > 1 else 0))
        order = np.argsort(-r[ys, xs])
        taken = np.zeros_like(r, dtype=bool)
        hh, ww = t_s.shape
        for k in order[:4000]:
            y, x = ys[k], xs[k]
            if taken[y, x]:
                continue
            taken[max(0, y - hh // 2):y + hh // 2 + 1, max(0, x - ww // 2):x + ww // 2 + 1] = True
            X, Y = x * f, y * f
            if f > 1:
                Y0, X0 = max(0, Y - 2 * f), max(0, X - 2 * f)
                win = hay[Y0:Y + t.shape[0] + 2 * f, X0:X + t.shape[1] + 2 * f]
                if win.shape[0] < t.shape[0] or win.shape[1] < t.shape[1]:
                    continue
                rr = cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED)
                _, sc, _, (dx, dy) = cv2.minMaxLoc(rr)
                X, Y = X0 + dx, Y0 + dy
            else:
                sc = float(r[y, x])
            if sc >= lo:
                hits.append(dict(bbox=[int(X + ox), int(Y + oy), int(X + ox + t.shape[1]), int(Y + oy + t.shape[0])], score=round(float(sc), 3), rot=rot, mirror=flip))
    return hits


def _dedupe(hits):
    hits = sorted(hits, key=lambda h: -h["score"])
    keep = []
    for h in hits:  # one hit per place across variants, tiles and examples
        b = h["bbox"]
        if all(min(b[2], k["bbox"][2]) - max(b[0], k["bbox"][0]) < 0.5 * (b[2] - b[0]) or min(b[3], k["bbox"][3]) - max(b[1], k["bbox"][1]) < 0.5 * (b[3] - b[1]) for k in keep):
            keep.append(h)
    return keep


def _find(img, box, lo=0.7, rot=False, scales=None, region=None):
    """All look-alikes of the example box; a large sheet is searched in 3000 px tiles (overlapping by a symbol)."""
    x0, y0, x1, y1 = [int(v) for v in box]
    tpl = img[y0:y1, x0:x1]
    frac = float((tpl < INK).mean()) if tpl.size else 0.0
    if frac < 0.03:
        raise ValueError(f"the example box {box} holds almost no ink ({frac:.1%}): box the symbol itself, not the paper around it")
    variants = _variants(tpl, rot, scales)
    R = region or [0, 0, img.shape[1], img.shape[0]]
    th = max(t.shape[0] for _, _, t in variants); tw = max(t.shape[1] for _, _, t in variants)
    T, ov = 3000, max(th, tw) + 8
    hits = []
    for ty in range(R[1], R[3], T):
        for tx in range(R[0], R[2], T):
            X1, Y1 = min(R[2], tx + T + ov), min(R[3], ty + T + ov)
            hay = img[ty:Y1, tx:X1]
            if hay.shape[0] > th and hay.shape[1] > tw and (hay < INK).any():
                hits += _match_region(hay, variants, lo, tx, ty)
    return _dedupe(hits)


def find_like(a):
    img = load(a[2]); box = [int(float(v)) for v in a[3:7]]; out = a[7]
    reg = opt(a, "--in", 4, lambda v: int(float(v)))
    lo = opt(a, "--min", default=0.7)
    try:
        keep = _find(img, box, lo, "--rot" in a, opt(a, "--scales", 3, float) if "--scales" in a else None, reg)
    except ValueError as e:
        sys.exit(str(e))
    json.dump(keep, open(out, "w"), indent=0)
    print(f"{len(keep)} hits >= {lo} (best {keep[0]['score'] if keep else '-'}, worst {keep[-1]['score'] if keep else '-'}) -> {out}")
    if "--sheet" in a:
        crops(["", "", a[2], out, a[a.index("--sheet") + 1], "--pad", "20"])


def auto(a):
    """auto <image> <classes.json> <outdir> [--min 0.72] [--review 0.55] [--scales 0.85 1.2 1.4]
    Every box class of a survey: find_like from each of its examples (rotations, mirrors and sizes), merged.
    Hits >= --min are accepted, --review..--min go to a review sheet. Writes <outdir>/<class>.json
    ({"accepted": [...], "review": [...]}) and <class>_ok.png / <class>_review.png contact sheets, numbered,
    plus summary.json. Polyline / polygon classes are skipped (use trace / fill)."""
    import os
    img = load(a[2]); classes = json.load(open(a[3]))["classes"]; od = a[4]; os.makedirs(od, exist_ok=True)
    hi = opt(a, "--min", default=0.72); lo = opt(a, "--review", default=0.55)
    scales = opt(a, "--scales", 3, float) if "--scales" in a else [0.85, 1.2]
    summary = {}
    for c in classes:
        exs = [e["bbox"] for e in c.get("examples") or [] if e.get("bbox")]
        if c.get("geometry") != "box" or not exs:
            summary[c["name"]] = "skipped (not a box class, or no box example)"; continue
        hits = []
        for e in exs:
            try:
                hits += _find(img, e, lo, True, scales)
            except ValueError as err:
                summary.setdefault(c["name"] + " notes", []).append(str(err))
        hits = _dedupe(hits)
        ok = [h for h in hits if h["score"] >= hi]; rv = [h for h in hits if h["score"] < hi]
        name = c["name"]
        est = c.get("estimated_count")
        est = est if isinstance(est, (int, float)) else None
        if est is not None and len(ok) > 3 * est + 6:  # a plain box matches everything: the template says nothing
            json.dump(dict(status="not distinctive", accepted=[], review=[]), open(f"{od}/{name}.json", "w"))
            summary[name] = dict(status="not distinctive: template matching finds %d look-alikes for about %s; measure it another way" % (len(ok), est))
            print(f"{name:28s} NOT DISTINCTIVE ({len(ok)} look-alikes, survey estimate {est}): measure it another way", flush=True)
            continue
        json.dump(dict(accepted=ok, review=rv), open(f"{od}/{name}.json", "w"), indent=0)
        for lab, hs in (("ok", ok), ("review", rv)):
            if hs:
                json.dump(hs, open(f"{od}/_{name}_{lab}.json", "w"))
                crops(["", "", a[2], f"{od}/_{name}_{lab}.json", f"{od}/{name}_{lab}.png", "--pad", "16", "--cols", "10", "--max", "60" if lab == "ok" else "30"])
        summary[name] = dict(accepted=len(ok), review=len(rv), estimated=c.get("estimated_count"))
        print(f"{name:28s} accepted {len(ok):3d}  review {len(rv):3d}  (survey estimate {c.get('estimated_count')})", flush=True)
    json.dump(summary, open(f"{od}/summary.json", "w"), indent=1)


INK = int(sys.argv[sys.argv.index("--ink") + 1]) if "--ink" in sys.argv else 170


def ink_mask(img):
    return (img < INK).astype(np.uint8)


def snap_box(a):
    img = load(a[2]); x0, y0, x1, y1 = [int(float(v)) for v in a[3:7]]
    pad = int(opt(a, "--pad", default=6))
    bw, bh = max(4, x1 - x0), max(4, y1 - y0)
    X0, Y0 = max(0, x0 - 2 * bw), max(0, y0 - 2 * bh)
    X1, Y1 = min(img.shape[1], x1 + 2 * bw), min(img.shape[0], y1 + 2 * bh)
    m = ink_mask(img[Y0:Y1, X0:X1])
    # walls and long lines the object touches: straight runs longer than 1.5x the box, removed before boxing the object
    lh = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(1.5 * bw) + 1, 1)))
    lv = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(1.5 * bh) + 1)))
    obj = m & ~(lh | lv) & 1
    bx = [max(0, x0 - X0 - pad), max(0, y0 - Y0 - pad), x1 - X0 + pad, y1 - Y0 + pad]
    win = obj[bx[1]:bx[3], bx[0]:bx[2]]
    ys, xs = np.nonzero(win)
    if len(xs) < 3:
        print("no ink of an object in the box (only long lines, or paper)"); return
    b = [int(xs.min()) + bx[0] + X0, int(ys.min()) + bx[1] + Y0, int(xs.max()) + 1 + bx[0] + X0, int(ys.max()) + 1 + bx[1] + Y0]
    print(json.dumps(dict(bbox=[int(v) for v in b], moved_px=int(max(abs(b[0] - x0), abs(b[1] - y0), abs(b[2] - x1), abs(b[3] - y1))))))


def snap_line(a):
    img = load(a[2]); x0, y0, x1, y1 = [float(v) for v in a[3:7]]
    S = int(opt(a, "--search", default=60)); mpp = opt(a, "--mpp")
    horiz = abs(x1 - x0) >= abs(y1 - y0)
    if horiz:
        lo, hi, c = int(min(x0, x1)), int(max(x0, x1)), (y0 + y1) / 2
        band = ink_mask(img[max(0, int(c) - S):int(c) + S, lo:hi])
        prof = band.mean(axis=1); base = max(0, int(c) - S)
    else:
        lo, hi, c = int(min(y0, y1)), int(max(y0, y1)), (x0 + x1) / 2
        band = ink_mask(img[lo:hi, max(0, int(c) - S):int(c) + S])
        prof = band.mean(axis=0); base = max(0, int(c) - S)
    lines = [i for i in range(1, len(prof) - 1) if prof[i] >= 0.5 and prof[i] >= prof[i - 1] and prof[i] >= prof[i + 1]]
    merged = []
    for i in lines:  # one position per drawn line
        if merged and i - merged[-1][-1] <= 3:
            merged[-1].append(i)
        else:
            merged.append([i])
    pos = [base + sum(g) / len(g) for g in merged]
    if not pos:
        print("no drawn line along this segment (less than half of it is ink at any offset)"); return
    best = None
    for i in range(len(pos)):
        for j in range(i + 1, len(pos)):
            gap = pos[j] - pos[i]
            mid = (pos[i] + pos[j]) / 2
            if 6 <= gap <= 2 * S - 4:
                inner = prof[int(pos[i] - base) + 3:int(pos[j] - base) - 2]
                if len(inner) and inner.mean() < 0.15 and (best is None or abs(mid - c) < abs(best[0] - c)):
                    best = (mid, gap)
    if best:
        mid, gap = best
        res = dict(kind="line pair", centre=round(mid, 1), width_px=round(gap, 1), moved_px=round(abs(mid - c), 1))
        if mpp:
            res.update(width_mm=round(gap * mpp * 1000), width_in=round(gap * mpp / 0.0254, 1))
    else:
        p = min(pos, key=lambda v: abs(v - c))
        res = dict(kind="single line", centre=round(p, 1), moved_px=round(abs(p - c), 1))
    seg = [[lo, res["centre"]], [hi, res["centre"]]] if horiz else [[res["centre"], lo], [res["centre"], hi]]
    res["polyline"] = seg
    print(json.dumps(res))


def fill(a):
    img = load(a[2]); x, y = int(float(a[3])), int(float(a[4]))
    close = int(opt(a, "--close", default=0)); mx = opt(a, "--max_frac", default=0.25)
    walls = ink_mask(img)
    if close:
        walls = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (close, close)))
    i = 0
    while "--bar" in a[i:]:  # temporary barrier lines across openings (a door, an open side): --bar x0 y0 x1 y1
        i = a.index("--bar", i) + 1
        bx = [int(float(v)) for v in a[i:i + 4]]
        cv2.line(walls, (bx[0], bx[1]), (bx[2], bx[3]), 1, 3)
    if walls[y, x]:
        print("the point is on ink, not paper: move it into the open area"); return
    free = (1 - walls).astype(np.uint8)
    mask = np.zeros((free.shape[0] + 2, free.shape[1] + 2), np.uint8)
    cv2.floodFill(free, mask, (x, y), 2, flags=8)
    reg = (free == 2).astype(np.uint8)
    area = int(reg.sum())
    if area > mx * reg.size:
        print(f"leaked: {area} px is over {mx:.0%} of the sheet; the outline is open somewhere (try --close 15)"); return
    reg = cv2.morphologyEx(reg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))  # furniture lines inside the room
    cs, _ = cv2.findContours(reg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(cs, key=cv2.contourArea)
    poly = cv2.approxPolyDP(c, 3, True)[:, 0, :].tolist()
    print(json.dumps(dict(polygon=poly, area_px=int(cv2.contourArea(c)), corners=len(poly))))


def crops(a):
    from PIL import Image as I, ImageDraw, ImageFont
    I.MAX_IMAGE_PIXELS = None
    src, items_path, out = a[2], a[3], a[4]
    pad = int(opt(a, "--pad", default=40)); cols = int(opt(a, "--cols", default=6)); mx = int(opt(a, "--max", default=60))
    items = json.load(open(items_path))
    if isinstance(items, dict) and "classes" in items:  # a discovered.json: --class NAME picks one class (default: all)
        want = a[a.index("--class") + 1] if "--class" in a else None
        items = [dict(it, label=it.get("label") or it.get("tag") or c["name"]) for c in items["classes"] if want in (None, c["name"]) for it in c.get("items") or []]
    elif isinstance(items, dict):
        items = items.get("items") or items.get("hits") or []
    im = I.open(src).convert("RGB")
    tiles = []
    for k, it in enumerate(items[:mx]):
        pts = it.get("polyline") or it.get("polygon") or ([it["point"]] if "point" in it else None)
        b = it.get("bbox") or ([min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)] if pts else None)
        if not b:
            continue
        X0, Y0, X1, Y1 = int(b[0]) - pad, int(b[1]) - pad, int(b[2]) + pad, int(b[3]) + pad
        t = im.crop((X0, Y0, X1, Y1))
        s = 260 / max(t.width, t.height)  # small plans are enlarged, big crops shrunk: every tile about 260 px
        t = t.resize((max(1, int(t.width * s)), max(1, int(t.height * s))))
        d = ImageDraw.Draw(t)
        P = lambda p: ((p[0] - X0) * s, (p[1] - Y0) * s)
        if it.get("polyline"):
            d.line([P(p) for p in it["polyline"]], fill=(220, 30, 30), width=2)
        elif it.get("polygon"):
            d.polygon([P(p) for p in it["polygon"]], outline=(220, 30, 30))
        elif "point" in it:
            px, py = P(it["point"]); d.ellipse([px - 4, py - 4, px + 4, py + 4], outline=(220, 30, 30), width=2)
        else:
            d.rectangle([P(b[:2]), P(b[2:])], outline=(220, 30, 30), width=2)
        lab = f"{k}" + (f" {it.get('label') or it.get('tag') or ''}" if (it.get('label') or it.get('tag')) else "") + (f" {it['score']}" if "score" in it else "")
        d.rectangle([0, 0, 8 * len(lab) + 6, 16], fill=(255, 255, 255)); d.text((3, 2), lab, fill=(0, 0, 0))
        tiles.append(t)
    if not tiles:
        print("no items"); return
    cw = max(t.width for t in tiles) + 6; ch = max(t.height for t in tiles) + 6
    rows = (len(tiles) + cols - 1) // cols
    sheet = I.new("RGB", (cols * cw, rows * ch), (235, 235, 235))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * cw + 3, (k // cols) * ch + 3))
    sheet.save(out)
    print(f"{len(tiles)} crops -> {out}" + (f" (first {mx} of {len(items)})" if len(items) > mx else ""))


def _profile(ink, p, th, S, along=2):
    """Ink along the normal of heading th at point p, from -S to +S (averaged over a few px along the heading)."""
    n = np.array([-np.sin(th), np.cos(th)]); t = np.array([np.cos(th), np.sin(th)])
    offs = np.arange(-S, S + 1)
    acc = np.zeros(len(offs))
    for k in range(-along, along + 1):
        q = p[None, :] + offs[:, None] * n[None, :] + k * t[None, :]
        xi = np.clip(np.round(q[:, 0]).astype(int), 0, ink.shape[1] - 1); yi = np.clip(np.round(q[:, 1]).astype(int), 0, ink.shape[0] - 1)
        acc += ink[yi, xi]
    return acc / (2 * along + 1) >= 0.5


def _runs(prof):
    r, i = [], 0
    while i < len(prof):
        if prof[i]:
            j = i
            while j + 1 < len(prof) and prof[j + 1]:
                j += 1
            r.append((i, j)); i = j + 1
        else:
            i += 1
    return r


def _pair(prof, S, w0):
    """Best (offset of the midpoint from centre, width) of two edges with mostly paper between; None if none."""
    runs = [r for r in _runs(prof) if r[1] - r[0] <= 12]
    best = None
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            a, b = runs[i], runs[j]
            w = (b[0] + b[1]) / 2 - (a[0] + a[1]) / 2
            if w < 6 or (w0 and abs(w - w0) > max(4, 0.22 * w0)):
                continue
            inner = prof[a[1] + 1:b[0]]
            if len(inner) == 0 or inner.mean() > 0.2:
                continue
            mid = (a[0] + a[1] + b[0] + b[1]) / 4 - S
            score = abs(mid) + (abs(w - w0) * 0.5 if w0 else 0)
            if best is None or score < best[2]:
                best = (mid, w, score)
    return best and best[:2]


def _single(prof, S):
    runs = [r for r in _runs(prof) if r[1] - r[0] <= 8]
    if not runs:
        return None
    r = min(runs, key=lambda r: abs((r[0] + r[1]) / 2 - S))
    return ((r[0] + r[1]) / 2 - S, 0.0)


def trace(a):
    img = load(a[2]); ink = (img < INK).astype(np.uint8)
    p = np.array([float(a[3]), float(a[4])]); q = np.array([float(a[5]), float(a[6])])
    th = float(np.arctan2(q[1] - p[1], q[0] - p[0]))
    mx = int(opt(a, "--max", default=6000)); mpp = opt(a, "--mpp")
    S = 70
    first = _pair(_profile(ink, p, th, S), S, None)
    mode = "pair" if first else "single"
    first = first or _single(_profile(ink, p, th, S), S)
    if not first:
        print("no drawn line or line pair at the start point"); return
    p = p + first[0] * np.array([-np.sin(th), np.cos(th)])
    w0 = first[1] if mode == "pair" else 0.0
    S = int(max(25, 1.6 * w0 + 12)) if mode == "pair" else 25
    step = max(3.0, w0 / 6) if mode == "pair" else 3.0
    pts, widths, coast, walked, why = [p.copy()], [w0], 0.0, 0.0, "reached --max"
    hist = [p.copy()]
    while walked < mx:
        found = None
        tol_w = max(3, 0.12 * w0) if mode == "pair" else 0
        tol_o = max(2.5, 0.12 * w0) if mode == "pair" else 2.5
        for d in (0, -4, 4, -8, 8, -12, 12, -17, 17, -23, 23):  # an elbow turns ~6-20 degrees per step
            t2 = th + np.radians(d)
            c = p + step * np.array([np.cos(t2), np.sin(t2)])
            prof = _profile(ink, c, t2, S)
            r = _pair(prof, S, w0) if mode == "pair" else _single(prof, S)
            if r and abs(r[0]) <= tol_o and (mode != "pair" or abs(r[1] - w0) <= tol_w):
                m = c + r[0] * np.array([-np.sin(t2), np.cos(t2)])
                for _ in range(2):  # square the cut to the pipe: heading from the last point to this centre, cut again
                    v = m - p
                    t3 = float(np.arctan2(v[1], v[0]))
                    c3 = p + step * np.array([np.cos(t3), np.sin(t3)])
                    r3 = _pair(_profile(ink, c3, t3, S), S, w0) if mode == "pair" else _single(_profile(ink, c3, t3, S), S)
                    if not (r3 and abs(r3[0]) <= tol_o):
                        break
                    m, r, t2 = c3 + r3[0] * np.array([-np.sin(t3), np.cos(t3)]), r3, t3
                found = (m, r[1])
                break
        if found:
            p, w = found; coast = 0.0
            hist.append(p.copy())
            back = hist[max(0, len(hist) - 3):]  # heading from the last two steps: keeps the cut square on bends
            if len(back) >= 2:
                v = back[-1] - back[0]
                if np.linalg.norm(v) > 1:
                    th = float(np.arctan2(v[1], v[0]))
            if mode == "pair":
                w0 = 0.95 * w0 + 0.05 * w
            pts.append(p.copy()); widths.append(w)
        else:  # a valve, flange, tee or cross mark: keep going straight for a while
            p = p + step * np.array([np.cos(th), np.sin(th)]); coast += step
            if coast > max(60, 3.5 * (w0 or 10)):
                why = "no matching line ahead within 3.5 widths (end of the run, a tee, or a large fitting)"
                break
        walked += step
        if not (0 <= p[0] < img.shape[1] and 0 <= p[1] < img.shape[0]):
            why = "left the sheet"; break
    arr = np.array(pts, dtype=np.float32).reshape(-1, 1, 2)
    simp = cv2.approxPolyDP(arr, 1.0, False)[:, 0, :].tolist() if len(pts) > 2 else [list(map(float, x)) for x in pts]
    wpx = float(np.median(widths)) if mode == "pair" else 0.0
    res = dict(kind=mode, polyline=[[round(x, 1), round(y, 1)] for x, y in simp], length_px=round(float(sum(np.linalg.norm(np.diff(np.array(pts), axis=0), axis=1))), 1),
               width_px=round(wpx, 1), stopped=why)
    if mpp and wpx:
        res.update(width_in=round(wpx * mpp / 0.0254, 1), width_mm=round(wpx * mpp * 1000))
    if "--out" in a:
        json.dump(res, open(a[a.index("--out") + 1], "w"))
    print(json.dumps(res) if len(json.dumps(res)) < 1500 else json.dumps(dict(res, polyline=f"{len(res['polyline'])} points (use --out)")))


def lint_layer(a):
    """lint_layer <discovered.json> [<image>]: flags items that are probably noise or mistakes, in any class: ragged
    outlines (many corners, uneven shape), items far bigger or smaller than the rest of their class, near-duplicates in
    one class, items outside the image, zero-size items. Every flag should be fixed (redraw the item on the real edges)
    or the item dropped."""
    from shapely.geometry import Polygon, box as sbox
    d = json.load(open(a[2]))
    W = H = None
    if len(a) > 3 and not a[3].startswith("--"):
        g = load(a[3]); H, W = g.shape
    flags = []
    for c in d.get("classes", []):
        geos = []
        for k, it in enumerate(c.get("items") or []):
            pts = it.get("polygon")
            if pts and len(pts) >= 3:
                g = Polygon(pts).buffer(0)
                sol = g.area / (g.convex_hull.area or 1)
                if len(pts) > 30 and sol < 0.85:
                    flags.append(f"{c['name']} #{k}: ragged outline ({len(pts)} corners, fills {sol:.0%} of its hull): does it follow real edges?")
            elif it.get("bbox"):
                b = it["bbox"]; g = sbox(*b) if b[2] > b[0] and b[3] > b[1] else None
                if g is None:
                    flags.append(f"{c['name']} #{k}: zero-size box {b}"); continue
            else:
                continue
            if W and (g.bounds[0] < -2 or g.bounds[1] < -2 or g.bounds[2] > W + 2 or g.bounds[3] > H + 2):
                flags.append(f"{c['name']} #{k}: outside the image")
            geos.append((k, g))
        boxes_only = all("bbox" in it and "polygon" not in it and "polyline" not in it for it in c.get("items") or [])
        if len(geos) >= 4 and boxes_only:  # size outliers only for symbol classes; walls and rooms vary in size anyway
            areas = sorted(g.area for _, g in geos); med = areas[len(areas) // 2] or 1
            for k, g in geos:
                if g.area > 6 * med or g.area < med / 6:
                    flags.append(f"{c['name']} #{k}: {g.area / med:.1f}x the class's median size: same kind of thing?")
        for i in range(len(geos)):
            for j in range(i + 1, len(geos)):
                (ki, gi), (kj, gj) = geos[i], geos[j]
                inter = gi.intersection(gj).area
                if inter > 0.8 * min(gi.area, gj.area):
                    flags.append(f"{c['name']} #{ki} and #{kj}: overlap {inter / min(gi.area, gj.area):.0%}: duplicate?")
    print("\n".join(flags) if flags else "no problems found")


if __name__ == "__main__":
    {"lint_layer": lint_layer, "auto": auto, "trace": trace, "find_like": find_like, "snap_box": snap_box, "snap_line": snap_line, "fill": fill, "crops": crops}[sys.argv[1]](sys.argv)
