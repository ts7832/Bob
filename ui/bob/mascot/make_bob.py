"""Bob from the reference picture (ui/bob/mascot/src/bob_reference.webp), turned into one-bit pixel art.

    python ui/bob/mascot/make_bob.py [--variants]

The photo is cut out from its white background, shrunk to a pixel grid, its tones mapped through an ordered (Bayer)
dither to pure black and white, and outlined in black so it reads like the rest of the One-bit UI.
"""
import os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src", "bob_reference.webp")
INK, PAPER = (30, 30, 30, 255), (254, 254, 254, 255)
BAYER2 = np.array([[0, 2], [3, 1]]) / 4
BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16


def cutout(im):
    """Mask of the figure: everything that is not near-white background, flood-filled from the borders."""
    a = np.asarray(im.convert("RGB")).astype(int)
    bg = (a.min(axis=2) > 236)
    from collections import deque
    h, w = bg.shape
    seen = np.zeros_like(bg)
    q = deque([(0, x) for x in range(w)] + [(h - 1, x) for x in range(w)] + [(y, 0) for y in range(h)] + [(y, w - 1) for y in range(h)])
    while q:
        y, x = q.popleft()
        if 0 <= y < h and 0 <= x < w and not seen[y, x] and bg[y, x]:
            seen[y, x] = True
            q.extend(((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)))
    return ~seen


def onebit(height=128, bayer=4, gamma=0.85, outline=True, contrast=1.25, bright=0.0, im=None):
    im = im or Image.open(SRC).convert("RGB")
    mask = Image.fromarray((cutout(im) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))
    w = round(im.width * height / im.height)
    g = ImageOps.grayscale(im)
    g = ImageOps.autocontrast(g, cutoff=1)
    small = np.asarray(g.resize((w, height), Image.LANCZOS)).astype(float) / 255
    small = np.clip((small - 0.5) * contrast + 0.5 + bright, 0, 1) ** gamma
    m = np.asarray(mask.resize((w, height), Image.LANCZOS)) > 110
    B = BAYER4 if bayer == 4 else BAYER2
    th = np.tile(B, (height // B.shape[0] + 1, w // B.shape[1] + 1))[:height, :w]
    ink = small < th * 0.92 + 0.04                       # darker than the threshold -> black
    if outline:
        e = np.asarray(g.filter(ImageFilter.FIND_EDGES).resize((w, height), Image.LANCZOS)).astype(float) / 255
        ink |= (e > 0.22) & m
        # the silhouette's own edge
        mm = m.astype(np.uint8)
        inner = mm.copy()
        inner[1:-1, 1:-1] = mm[1:-1, 1:-1] & mm[:-2, 1:-1] & mm[2:, 1:-1] & mm[1:-1, :-2] & mm[1:-1, 2:]
        ink |= (mm == 1) & (inner == 0)
    out = np.zeros((height, w, 4), np.uint8)
    out[m & ink] = INK
    out[m & ~ink] = PAPER
    return Image.fromarray(out, "RGBA")


def flat(height=160, levels=(0.30, 0.55, 0.80), outline=True, im=None, edge=0.16):
    """Cartoon one-bit: each pixel's tone falls in one of four flat fills (black, checker, sparse dots, white), and
    colour edges (hat vs face, shirt check, overalls) are drawn as black lines."""
    im = im or Image.open(SRC).convert("RGB")
    mask = Image.fromarray((cutout(im) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))
    w = round(im.width * height / im.height)
    sm = im.resize((w, height), Image.LANCZOS)
    a = np.asarray(sm).astype(float) / 255
    tone = 0.30 * a[..., 0] + 0.59 * a[..., 1] + 0.11 * a[..., 2]
    sat = a.max(axis=2) - a.min(axis=2)
    tone = np.clip(tone - 0.10 * (sat > 0.45) * (a[..., 0] > a[..., 1] + 0.25), 0, 1)   # strong reds (the shirt check) read darker
    m = np.asarray(mask.resize((w, height), Image.LANCZOS)) > 110
    yy, xx = np.mgrid[0:height, 0:w]
    checker = (xx + yy) % 2 == 0
    dots = (xx % 2 == 0) & (yy % 2 == 0)
    ink = np.where(tone < levels[0], True, np.where(tone < levels[1], checker, np.where(tone < levels[2], dots, False)))
    if outline:
        big = im.resize((w * 2, height * 2), Image.LANCZOS)
        e = np.zeros((height * 2, w * 2))
        for c in big.split():
            e = np.maximum(e, np.asarray(c.filter(ImageFilter.FIND_EDGES)).astype(float) / 255)
        e = np.asarray(Image.fromarray((e * 255).astype(np.uint8)).resize((w, height), Image.BOX)).astype(float) / 255
        ink |= (e > edge) & m
        mm = m.astype(np.uint8)
        inner = mm.copy()
        inner[1:-1, 1:-1] = mm[1:-1, 1:-1] & mm[:-2, 1:-1] & mm[2:, 1:-1] & mm[1:-1, :-2] & mm[1:-1, 2:]
        ink |= (mm == 1) & (inner == 0)
    out = np.zeros((height, w, 4), np.uint8)
    out[m & ink] = INK
    out[m & ~ink] = PAPER
    return Image.fromarray(out, "RGBA")


def wave_frames(height, **kw):
    """The raised hand waves: the hand region rotated a little about the wrist, frame by frame."""
    base = Image.open(SRC).convert("RGB")
    W, H = base.size
    box = (0, 0, int(W * 0.34), int(H * 0.27))           # the raised hand, top left of the reference
    wrist = (int(W * 0.17), int(H * 0.25))
    frames = []
    for ang in (0, 7, 13, 7, 0, -6, 0, 7, 13, 7):
        im = base.copy()
        if ang:
            hand = base.crop(box)
            cover = Image.new("RGB", hand.size, (255, 255, 255))
            im.paste(cover, box[:2])
            # keep the forearm below the box untouched; rotate the hand about the wrist
            rot = hand.rotate(ang, resample=Image.BICUBIC, center=(wrist[0] - box[0], wrist[1] - box[1]), fillcolor=(255, 255, 255))
            im.paste(rot, box[:2])
        frames.append(flat(height, im=im, levels=(0.28, 0.52, 0.70), edge=0.20))   # the chosen look (F7): flat tones, white face
    return frames


def sheet(frames, path):
    w, h = frames[0].size
    s = Image.new("RGBA", (w * len(frames), h), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        s.paste(f, (i * w, 0))
    s.save(path)
    return w, h, len(frames)


if __name__ == "__main__":
    if "--variants" in sys.argv:
        V = [dict(height=96, bayer=2), dict(height=128, bayer=2), dict(height=128, bayer=4), dict(height=160, bayer=4),
             dict(height=160, bayer=4, outline=False), dict(height=200, bayer=4), dict(height=128, bayer=4, contrast=1.6, gamma=1.1),
             dict(height=160, bayer=2, contrast=1.5)]
        for i, v in enumerate(V):
            onebit(**v).save(os.path.join(HERE, "src", f"variant_{i + 1}.png"))
        F = [dict(height=128), dict(height=160), dict(height=200), dict(height=160, edge=0.10), dict(height=160, levels=(0.25, 0.5, 0.78), edge=0.22),
             dict(height=240)]
        for i, v in enumerate(F):
            flat(**v).save(os.path.join(HERE, "src", f"flat_{i + 1}.png"))
            print("flat", i + 1, v)
    elif "--icons" not in sys.argv:
        h = int(os.environ.get("BOB_HEIGHT", "200"))
        print("wave.png", sheet(wave_frames(h, bayer=4), os.path.join(HERE, "wave.png")))


# ---------------------------------------------------------------------------------------------------------------- 32 x 32 icons
def crop_ref(box):
    """A part of the reference (fractions of the picture) on a white square, so it can be shrunk to an icon."""
    im = Image.open(SRC).convert("RGB")
    W, H = im.size
    x0, y0, x1, y1 = int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H)
    c = im.crop((x0, y0, x1, y1))
    side = max(c.size)
    sq = Image.new("RGB", (side, side), (255, 255, 255))
    sq.paste(c, ((side - c.width) // 2, side - c.height))
    return sq


def icon32(box, levels=(0.30, 0.55, 0.78), edge=0.20, size=32):
    return flat(height=size, im=crop_ref(box), levels=levels, edge=edge)


def with_clipboard(ic, t):
    """A clipboard held in front of the chest and a pencil writing on it (frame t)."""
    d = ImageDraw.Draw(ic)
    x0, y0, x1, y1 = 15, 19, 29, 31
    d.rectangle([x0, y0, x1, y1], fill=PAPER, outline=INK)
    d.rectangle([20, 17, 24, 20], fill=INK)
    for i, y in enumerate((23, 26, 29)):
        n = 9 if i < t // 3 else (t % 3) * 3
        if n:
            d.line([17, y, 17 + n, y], fill=INK)
    px, py = 17 + (t % 3) * 3 + 2, 23 + min(2, t // 3) * 3
    d.line([px, py, px + 5, py - 6], fill=INK, width=2)
    d.point((px, py), fill=INK)
    return ic


ICON_BOXES = {"i1_full": (0.0, 0.0, 1.0, 1.0), "i2_bust": (0.22, 0.0, 0.92, 0.42), "i3_head": (0.30, 0.0, 0.88, 0.30),
              "i4_wave": (0.0, 0.0, 0.92, 0.45), "i5_face": (0.36, 0.06, 0.84, 0.30)}
if __name__ == "__main__" and "--icons" in sys.argv:
    from PIL import ImageDraw
    os.makedirs(os.path.join(HERE, "icons"), exist_ok=True)
    for k, box in ICON_BOXES.items():
        icon32(box).save(os.path.join(HERE, "icons", k + ".png"))
        icon32(box, levels=(0.22, 0.45, 0.70), edge=0.26).save(os.path.join(HERE, "icons", k + "_light.png"))
    frames = [with_clipboard(icon32(ICON_BOXES["i2_bust"]), t) for t in range(9)]
    sheet(frames, os.path.join(HERE, "icons", "i6_clipboard.png"))
    frames = [with_clipboard(icon32(ICON_BOXES["i3_head"]), t) for t in range(9)]
    sheet(frames, os.path.join(HERE, "icons", "i7_head_clipboard.png"))
    print(sorted(os.listdir(os.path.join(HERE, "icons"))))
