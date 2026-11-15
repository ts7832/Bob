"""Bob as 32 x 32 one-bit pixel icons, placed by hand after the reference picture (src/bob_reference.webp): ridged hard
hat with brim, round head, big ears, round nose, wide smile, checked shirt (checker), white overall straps.

    python ui/bob/mascot/make_bob32.py      -> ui/bob/mascot/icons32/*.png (animated ones: frames side by side)
"""
import math, os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "icons32")
K, W, G, T = 0, 255, 128, 1


def onebit(im):
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    p, o = im.load(), out.load()
    for y in range(im.size[1]):
        for x in range(im.size[0]):
            v = p[x, y]
            if v == T:
                continue
            if v == G:
                v = K if (x // 1 + y // 1) % 2 == 0 else W
            o[x, y] = (30, 30, 30, 255) if v < 128 else (254, 254, 254, 255)
    return out


def new():
    im = Image.new("L", (32, 32), T)
    return im, ImageDraw.Draw(im)


def head(d, cx=16, top=1, look=0, mood="smile", wink=False, tilt=0):
    """Hat (dome, ridge, brim), face, ears, eyes, nose, smile. cx = centre column, top = top of the hat."""
    # ears first (behind the face)
    d.ellipse([cx - 12, top + 13, cx - 8, top + 19], fill=W, outline=K)
    d.ellipse([cx + 8, top + 13, cx + 12, top + 19], fill=W, outline=K)
    # face: big round head
    d.ellipse([cx - 9, top + 7, cx + 9, top + 25], fill=W, outline=K)
    # hard hat: dome + ridge + brim (white, the yellow of the original)
    d.pieslice([cx - 9, top, cx + 9, top + 18], 180, 360, fill=W, outline=K)
    d.line([cx, top + 1, cx, top + 8], fill=K)
    d.line([cx + 1, top + 1, cx + 1, top + 8], fill=K)
    d.rectangle([cx - 11, top + 9, cx + 11, top + 10], fill=W, outline=K)
    d.line([cx - 11, top + 11, cx + 11, top + 11], fill=K)
    # eyes (a little apart, cheerful), eyebrows
    ey = top + 14
    for ex in (cx - 5 + look, cx + 3 + look):
        if wink and ex > cx:
            d.line([ex, ey + 1, ex + 2, ey + 1], fill=K)
        else:
            d.rectangle([ex, ey, ex + 1, ey + 2], fill=K)
    d.line([cx - 6, ey - 2, cx - 3, ey - 2], fill=K)
    d.line([cx + 2, ey - 2, cx + 5, ey - 2], fill=K)
    # round nose
    d.ellipse([cx - 2 + look, top + 16, cx + 2 + look, top + 20], fill=W, outline=K)
    # mouth
    if mood == "smile":
        d.arc([cx - 6, top + 15, cx + 6, top + 22], 20, 160, fill=K)       # wide smile
        d.point((cx - 6, top + 19), fill=K); d.point((cx + 6, top + 19), fill=K)
    else:                                   # concentrating: a small straight mouth
        d.line([cx - 2, top + 21, cx + 2, top + 21], fill=K)


def shoulders(d, top=26):
    """Checked shirt (checker) with the white bib and straps of the overalls."""
    d.rounded_rectangle([3, top, 28, 33], radius=4, fill=G, outline=K)
    d.rectangle([11, top + 2, 20, 33], fill=W, outline=K)          # bib
    d.line([11, top + 2, 8, top], fill=K); d.line([20, top + 2, 23, top], fill=K)   # straps
    d.rectangle([14, top + 4, 17, top + 6], outline=K)              # bib pocket


def clipboard(d, t, x0=13, y0=19):
    x1, y1 = x0 + 15, y0 + 12
    d.rectangle([x0, y0, x1, y1], fill=W, outline=K)
    d.rectangle([x0 + 5, y0 - 2, x0 + 9, y0 + 1], fill=K)
    for i, y in enumerate((y0 + 4, y0 + 7, y0 + 10)):
        n = 10 if i < t // 3 else (t % 3) * 4
        if n:
            d.line([x0 + 2, y, x0 + 2 + n, y], fill=K)
    px, py = x0 + 2 + (t % 3) * 4 + 1, y0 + 4 + min(2, t // 3) * 3
    d.line([px, py, px + 4, py - 6], fill=K, width=2)
    d.line([px + 1, py - 2, px + 3, py - 5], fill=W)
    d.point((px, py), fill=K)


def wave_hand(d, ang, sx=3, sy=31):
    """Raised arm in the checked sleeve, open hand at the top."""
    a = math.radians(-100 + ang)
    hx, hy = sx + 14 * math.cos(a), sy + 14 * math.sin(a)
    d.line([sx, sy, hx, hy], fill=K, width=5)
    d.line([sx, sy, hx, hy], fill=G, width=3)
    d.ellipse([hx - 3, hy - 4, hx + 3, hy + 2], fill=W, outline=K)
    for fa in (-130, -105, -80, -55):
        b = math.radians(fa + ang)
        d.line([hx + 2 * math.cos(b), hy - 1 + 2 * math.sin(b), hx + 5 * math.cos(b), hy - 1 + 5 * math.sin(b)], fill=K)


def sheet(frames, name):
    s = Image.new("RGBA", (32 * len(frames), 32), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        s.paste(f, (i * 32, 0))
    s.save(os.path.join(OUT, name))
    return name, len(frames)


def b1_bust():
    im, d = new(); shoulders(d); head(d); return [onebit(im)]


def b2_wink():
    fr = []
    for w in (False, False, False, True):
        im, d = new(); shoulders(d); head(d, wink=w); fr.append(onebit(im))
    return fr


def b3_wave():
    fr = []
    for ang in (0, 12, 22, 12):
        im, d = new(); shoulders(d); head(d, cx=19); wave_hand(d, ang); fr.append(onebit(im))
    return fr


def b4_clipboard():                         # working: looks down, pencil writes on the clipboard
    fr = []
    for t in range(9):
        im, d = new(); shoulders(d); head(d, cx=12, look=1, mood="focus"); clipboard(d, t, x0=15, y0=18); fr.append(onebit(im))
    return fr


def b5_head():
    im, d = new(); head(d, top=4); return [onebit(im)]


def b6_head_clipboard():                    # working, head only above a notepad
    fr = []
    for t in range(9):
        im, d = new(); head(d, cx=16, top=0, look=(t % 3) - 1, mood="focus"); clipboard(d, t, x0=8, y0=19); fr.append(onebit(im))
    return fr


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, fn in [("b1_bust.png", b1_bust), ("b2_wink.png", b2_wink), ("b3_wave.png", b3_wave), ("b4_clipboard.png", b4_clipboard),
                     ("b5_head.png", b5_head), ("b6_head_clipboard.png", b6_head_clipboard)]:
        print(sheet(fn(), name))
