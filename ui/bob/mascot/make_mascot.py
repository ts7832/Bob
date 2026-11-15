"""Bob, the mascot: an original pixel builder in the One-bit style (black, white, checker dither for grey).

    python ui/bob/mascot/make_mascot.py        -> ui/bob/mascot/*.png (sprite sheets, frames side by side)

Drawn with shapes on a small grid, then turned one-bit: 0 = black, 255 = white, 128 = checker dither, 1 = transparent.
Scaled up in the page with image-rendering: pixelated. Not a copy of any existing character.
"""
import math, os, sys
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
K, W, G, T = 0, 255, 128, 1          # black, white, grey (dither), transparent


def onebit(im):
    """L image with K/W/G/T values -> RGBA one-bit with a checker for grey, transparency kept."""
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    px, o = im.load(), out.load()
    for y in range(im.size[1]):
        for x in range(im.size[0]):
            v = px[x, y]
            if v == T:
                continue
            if v == G:
                v = K if (x + y) % 2 == 0 else W
            o[x, y] = (30, 30, 30, 255) if v < 128 else (254, 254, 254, 255)
    return out


def sheet(frames, name):
    w, h = frames[0].size
    s = Image.new("RGBA", (w * len(frames), h), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        s.paste(f, (i * w, 0))
    s.save(os.path.join(HERE, name))
    return name, w, h, len(frames)


def canvas(w, h):
    im = Image.new("L", (w, h), T)
    return im, ImageDraw.Draw(im)


# ---------------------------------------------------------------------------------------------------------------- parts
def hat(d, cx, top, r, brim=None):
    """Hard hat: dithered dome with a black ridge, black brim."""
    d.pieslice([cx - r, top, cx + r, top + 2 * r], 180, 360, fill=G, outline=K)
    d.rectangle([cx - 1, top, cx + 1, top + r], fill=K)                       # ridge
    b = brim or r + 3
    d.rectangle([cx - b, top + r - 1, cx + b, top + r + 1], fill=K)           # brim


def face(d, cx, cy, r, mood="smile", look=0):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=W, outline=K)
    d.rectangle([cx - r - 2, cy - 1, cx - r, cy + 2], fill=W, outline=K)       # ears
    d.rectangle([cx + r, cy - 1, cx + r + 2, cy + 2], fill=W, outline=K)
    ey = cy - 1
    for ex in (cx - r // 2 - 1 + look, cx + r // 2 + look):
        d.rectangle([ex, ey, ex + 1, ey + 1], fill=K)
    d.point((cx + look, cy + 2), fill=K)                                       # nose
    if mood == "smile":
        d.arc([cx - r // 2 - 1, cy, cx + r // 2 + 1, cy + r - 1], 20, 160, fill=K)
    elif mood == "focus":
        d.line([cx - 2, cy + r // 2 + 1, cx + 2, cy + r // 2 + 1], fill=K)


def pencil(d, x, y, ang=-50, n=9):
    a = math.radians(ang)
    x2, y2 = x + n * math.cos(a), y + n * math.sin(a)
    d.line([x, y, x2, y2], fill=K, width=3)
    d.line([x, y, x2, y2], fill=W, width=1)
    d.point((round(x), round(y)), fill=K)                                      # tip


def notepad(d, x0, y0, x1, y1, lines=3, done=None, clip=True):
    d.rectangle([x0, y0, x1, y1], fill=W, outline=K)
    if clip:
        cx = (x0 + x1) // 2
        d.rectangle([cx - 3, y0 - 2, cx + 3, y0 + 1], fill=K)
    for i in range(lines):
        y = y0 + 4 + i * 3
        if y > y1 - 2:
            break
        ln = (x1 - x0 - 4) if done is None else min(x1 - x0 - 4, done[i] if i < len(done) else 0)
        if ln > 0:
            d.line([x0 + 2, y, x0 + 2 + ln, y], fill=K)


# ---------------------------------------------------------------------------------------------------------------- waving Bob
def waving(angle):
    """Full figure, 48 x 64, right hand (viewer's left) raised; angle swings the forearm."""
    im, d = canvas(48, 64)
    cx = 25
    # legs and boots
    d.rectangle([17, 50, 23, 58], fill=W, outline=K)
    d.rectangle([27, 50, 33, 58], fill=W, outline=K)
    d.rectangle([15, 58, 23, 62], fill=K)
    d.rectangle([27, 58, 35, 62], fill=K)
    # body: dithered shirt, white overalls with straps, belt with buckle and a hammer
    d.rectangle([15, 31, 35, 50], fill=G, outline=K)
    d.rectangle([18, 35, 32, 50], fill=W, outline=K)
    d.line([18, 31, 18, 35], fill=K); d.line([32, 31, 32, 35], fill=K)
    d.rectangle([21, 38, 29, 42], outline=K)                                  # bib pocket
    d.rectangle([15, 45, 35, 47], fill=K)
    d.rectangle([24, 45, 26, 47], fill=W, outline=K)                          # buckle
    d.rectangle([33, 46, 34, 53], fill=K); d.rectangle([31, 52, 36, 54], fill=K)   # hammer
    # left arm down (viewer's right)
    d.rectangle([35, 32, 39, 45], fill=G, outline=K)
    d.ellipse([34, 44, 40, 50], fill=W, outline=K)
    # raised arm (viewer's left): upper arm to the elbow, forearm swings
    sx, sy, ex, ey = 15, 33, 9, 27
    d.line([sx, sy, ex, ey], fill=K, width=5); d.line([sx, sy, ex, ey], fill=G, width=3)
    a = math.radians(-90 + angle)
    hx, hy = ex + 10 * math.cos(a), ey + 10 * math.sin(a)
    d.line([ex, ey, hx, hy], fill=K, width=5); d.line([ex, ey, hx, hy], fill=G, width=3)
    d.ellipse([hx - 3, hy - 4, hx + 3, hy + 2], fill=W, outline=K)            # palm
    for i, fa in enumerate((-125, -105, -85, -65)):                            # open fingers
        b = math.radians(fa + angle)
        d.line([hx + 1.5 * math.cos(b), hy - 1 + 1.5 * math.sin(b), hx + 5 * math.cos(b), hy - 1 + 5 * math.sin(b)], fill=K, width=1)
    d.line([hx - 3, hy, hx - 6, hy - 2], fill=K)                               # thumb
    # head
    face(d, cx, 23, 8)
    hat(d, cx, 6, 10, brim=13)
    return onebit(im)


def wave_frames():
    seq = [0, 14, 26, 14, 0, -10, 0, 14, 26, 14]
    fr = [waving(a) for a in seq]
    # a blink on one frame
    b = fr[4].copy(); bd = ImageDraw.Draw(b)
    for ex in (25 - 4 - 1, 25 + 4):
        bd.rectangle([ex, 22, ex + 1, 23], fill=(254, 254, 254, 255)); bd.line([ex, 23, ex + 1, 23], fill=(30, 30, 30, 255))
    fr[4] = b
    return fr


# ---------------------------------------------------------------------------------------------------------------- "working" icons (32 x 32)
def icon_a(t):   # 1. Bob's head over a clipboard, pencil writing
    im, d = canvas(32, 32)
    face(d, 16, 13, 6, "focus", look=0)
    hat(d, 16, 1, 8, brim=10)
    notepad(d, 8, 20, 24, 31, lines=3, done=[3 + t * 3, 12, 0] if t < 4 else [12, 3 + (t - 4) * 3, 0])
    pencil(d, 10 + (t % 4) * 3, 24 + (t >= 4) * 3, ang=-45, n=8)
    return onebit(im)


def icon_b(t):   # 2. full little Bob holding a clipboard, pencil moving
    im, d = canvas(32, 32)
    d.rectangle([11, 18, 21, 27], fill=G, outline=K)
    d.rectangle([12, 27, 15, 31], fill=K); d.rectangle([17, 27, 20, 31], fill=K)
    face(d, 16, 11, 5, "focus", look=1)
    hat(d, 16, 1, 6, brim=8)
    notepad(d, 17, 17, 27, 27, lines=3, done=[2 + t * 2, 6 if t > 2 else 0, 0], clip=True)
    pencil(d, 19 + (t % 3) * 2, 21 + (t > 2) * 3, ang=-60, n=7)
    return onebit(im)


def icon_c(t):   # 3. notepad close-up, a line being written, hard hat on the corner
    im, d = canvas(32, 32)
    notepad(d, 4, 8, 27, 30, lines=6, done=[19, 19, min(19, t * 4), 0, 0, 0] if t < 6 else [19, 19, 19, (t - 5) * 4, 0, 0])
    hat(d, 24, 1, 6, brim=8)
    pencil(d, 7 + min(19, t * 4 if t < 6 else (t - 5) * 4), 14 + (3 if t >= 6 else 0) + 3, ang=-40, n=9)
    return onebit(im)


def icon_d(t):   # 4. hard hat bobbing while a pencil scribbles a zigzag
    im, d = canvas(32, 32)
    hat(d, 16, 2 + (t % 2), 9, brim=12)
    pts = [(5 + i * 3, 24 + (2 if i % 2 else -1)) for i in range(min(8, t + 2))]
    if len(pts) > 1:
        d.line(pts, fill=K)
    px, py = pts[-1]
    pencil(d, px, py, ang=-55, n=9)
    d.line([4, 29, 28, 29], fill=K)
    return onebit(im)


def icon_e(t):   # 5. Bob peeking over the clipboard, eyes moving with the pencil
    im, d = canvas(32, 32)
    face(d, 16, 12, 7, "focus", look=(-1, 0, 1, 0)[t % 4])
    hat(d, 16, 0, 9, brim=11)
    d.rectangle([5, 17, 27, 31], fill=W, outline=K)
    d.rectangle([13, 15, 19, 18], fill=K)
    for i, y in enumerate((21, 24, 27)):
        d.line([8, y, 8 + (16 if i < t // 2 else (t % 2) * 8), y], fill=K)
    pencil(d, 10 + (t % 4) * 4, 22 + min(2, t // 2) * 3, ang=-50, n=8)
    return onebit(im)


def icon_f(t):   # 6. clipboard checklist, ticks appear one by one, tiny hat clipped on
    im, d = canvas(32, 32)
    d.rectangle([6, 5, 26, 31], fill=W, outline=K)
    d.rectangle([12, 2, 20, 6], fill=G, outline=K)
    for i, y in enumerate((11, 17, 23)):
        d.rectangle([9, y - 2, 12, y + 1], outline=K)
        d.line([15, y, 23, y], fill=K)
        if i < t:
            d.line([9, y - 1, 10, y + 1], fill=K); d.line([10, y + 1, 13, y - 3], fill=K)
    hat(d, 25, 0, 5, brim=6)
    return onebit(im)


def icon_g(t):   # 7. side view: Bob bent over a notepad on a sawhorse, arm moving
    im, d = canvas(32, 32)
    d.line([4, 24, 28, 24], fill=K, width=2)                                   # plank
    d.line([7, 24, 4, 31], fill=K); d.line([7, 24, 10, 31], fill=K)
    d.line([25, 24, 22, 31], fill=K); d.line([25, 24, 28, 31], fill=K)
    d.rectangle([14, 20, 25, 23], fill=W, outline=K)                           # notepad on the plank
    d.ellipse([3, 7, 13, 17], fill=W, outline=K)                               # head in profile
    d.rectangle([10, 11, 11, 12], fill=K)
    hat(d, 8, 2, 6, brim=8)
    d.line([9, 17, 13, 21], fill=K, width=3)                                   # shoulder to hand
    pencil(d, 15 + (t % 4) * 2, 21, ang=-70, n=7)
    return onebit(im)


def icon_h(t):   # 8. Bob holding up a rolled blueprint, unrolling it
    im, d = canvas(32, 32)
    face(d, 9, 12, 5, "smile")
    hat(d, 9, 2, 6, brim=8)
    d.rectangle([5, 18, 13, 29], fill=G, outline=K)
    w = 4 + t * 3
    d.rectangle([14, 8, 14 + min(w, 16), 26], fill=W, outline=K)
    for y in (12, 16, 20):
        d.line([16, y, 14 + min(w, 16) - 2, y], fill=K)
    d.ellipse([12 + min(w, 16), 6, 17 + min(w, 16), 28], fill=G, outline=K)  # the roll
    return onebit(im)


ICONS = [("work_a.png", icon_a, 8, "Head over a clipboard, writing"), ("work_b.png", icon_b, 6, "Little Bob with clipboard"),
         ("work_c.png", icon_c, 10, "Notepad close-up, hat on the corner"), ("work_d.png", icon_d, 7, "Bobbing hat, pencil scribble"),
         ("work_e.png", icon_e, 8, "Peeking over the clipboard"), ("work_f.png", icon_f, 4, "Checklist ticking off"),
         ("work_g.png", icon_g, 8, "Working at a sawhorse"), ("work_h.png", icon_h, 5, "Unrolling a blueprint")]

if __name__ == "__main__":
    out = [sheet(wave_frames(), "wave.png")]
    for name, fn, n, _ in ICONS:
        out.append(sheet([fn(t) for t in range(n)], name))
    for o in out:
        print(*o)


# ---------------------------------------------------------------------------------------------------------------- B + E combined (the user's pick)
def cute_head(d, cx, top, look=0, blink=False, mood="smile", r=5):
    """E's head: dithered hard hat with a ridge; eyes right under the brim, close together; round face; 1 px nose."""
    fy = top + 3                                                                           # face top (hidden by the brim)
    d.ellipse([cx - r, fy, cx + r, fy + 2 * r], fill=W, outline=K)
    d.rectangle([cx - r - 1, fy + 3, cx - r, fy + 6], fill=W, outline=K)                  # ears
    d.rectangle([cx + r, fy + 3, cx + r + 1, fy + 6], fill=W, outline=K)
    d.pieslice([cx - r - 2, top - 7, cx + r + 2, top + 9], 180, 360, fill=G, outline=K)   # dome
    d.rectangle([cx, top - 7, cx, top + 1], fill=K)                                       # ridge
    d.rectangle([cx - r - 4, top + 2, cx + r + 4, top + 3], fill=K)                       # brim
    ey = top + 4                                                                           # eyes touch the brim
    for ex in (cx - 3 + look, cx + 2 + look):
        if blink:
            d.point((ex, ey + 1), fill=K); d.point((ex + 1, ey + 1), fill=K)
        else:
            d.rectangle([ex, ey, ex + 1, ey + 1], fill=K)
    d.point((cx + look, ey + 3), fill=K)                                                  # nose
    if mood == "smile":
        d.arc([cx - 3, ey + 2, cx + 3, ey + 6], 20, 160, fill=K)
    else:
        d.line([cx - 1, ey + 5, cx + 1, ey + 5], fill=K)


def cute_body(d, cx, top, chub=0):
    """Round body: neck, dithered shirt, white bib, black boots (fits from top to top + 12)."""
    d.rectangle([cx - 1, top - 2, cx + 1, top], fill=W, outline=K)                       # neck joins head and body
    d.rounded_rectangle([cx - 6 - chub, top, cx + 6 + chub, top + 9], radius=4, fill=G, outline=K)
    d.rounded_rectangle([cx - 3, top + 2, cx + 3, top + 9], radius=1, fill=W, outline=K)  # bib
    d.line([cx - 3, top + 2, cx - 5, top], fill=K); d.line([cx + 3, top + 2, cx + 5, top], fill=K)  # straps
    d.rectangle([cx - 5, top + 9, cx - 1, top + 12], fill=K)                              # boots
    d.rectangle([cx + 1, top + 9, cx + 5, top + 12], fill=K)


def held_clipboard(d, x0, y0, t, w=10, h=11, lines=3):
    d.rectangle([x0, y0, x0 + w, y0 + h], fill=W, outline=K)
    d.rectangle([x0 + w // 2 - 2, y0 - 1, x0 + w // 2 + 2, y0 + 1], fill=K)              # clip
    full = w - 3
    for i in range(lines):
        y = y0 + 4 + i * 3
        n = full if i < t // 3 else (t % 3) * (full // 3 + 1)
        if n:
            d.line([x0 + 2, y, x0 + 2 + min(n, full), y], fill=K)
    return x0 + 2 + min(full, (t % 3) * (full // 3 + 1)), y0 + 4 + min(lines - 1, t // 3) * 3


def pen(d, x, y, n=6):
    d.line([x, y, x + n * 0.6, y - n], fill=K, width=2)
    d.point((x, y), fill=K)


def hand(d, x, y):
    d.rectangle([x - 1, y - 1, x + 1, y + 1], fill=W, outline=K)


def k1(t):   # standing, clipboard held at his side, writing; eyes follow the pencil, blinks once
    im, d = canvas(32, 32)
    cute_body(d, 12, 19)
    px, py = held_clipboard(d, 19, 15, t, w=10, h=12)
    hand(d, 18, 22)
    cute_head(d, 12, 7, look=1 if t % 3 else 0, blink=(t == 5), mood="focus" if t % 4 else "smile")
    pen(d, px, py)
    return onebit(im)


def k2(t):   # chubbier body, same writing
    im, d = canvas(32, 32)
    cute_body(d, 12, 19, chub=1)
    px, py = held_clipboard(d, 20, 15, t, w=10, h=12)
    hand(d, 19, 22)
    cute_head(d, 12, 7, look=1, blink=(t == 6))
    pen(d, px, py)
    return onebit(im)


def k3(t):   # nods on each stroke, smiles at the end of a line
    im, d = canvas(32, 32)
    cute_body(d, 12, 19)
    px, py = held_clipboard(d, 19, 15, t, w=10, h=12)
    hand(d, 18, 22)
    cute_head(d, 12, 7 + (1 if t % 3 == 1 else 0), look=1, mood="smile" if t % 3 == 2 else "focus")
    pen(d, px, py)
    return onebit(im)


def k4(t):   # E's pose with a body: clipboard up at the chest, peeking over it, boots below
    im, d = canvas(32, 32)
    cute_body(d, 16, 19)
    cute_head(d, 16, 7, look=(-1, 0, 1, 0)[t % 4], blink=(t == 7))
    px, py = held_clipboard(d, 9, 17, t, w=14, h=10)
    pen(d, px, py)
    return onebit(im)


def k5(t):   # waving hello, clipboard tucked under the other arm
    im, d = canvas(32, 32)
    cute_body(d, 15, 19)
    d.rectangle([21, 19, 27, 28], fill=W, outline=K)
    a = math.radians(-125 + (0, 15, 28, 15)[t % 4])
    hx, hy = 9 + 8 * math.cos(a), 21 + 8 * math.sin(a)
    d.line([9, 21, hx, hy], fill=K, width=3); d.line([9, 21, hx, hy], fill=W, width=1)
    hand(d, round(hx), round(hy))
    cute_head(d, 15, 7, blink=(t == 3))
    return onebit(im)


if __name__ == "__main__" and "--cute" in sys.argv:
    for name, fn, n in [("work_k1.png", k1, 9), ("work_k2.png", k2, 9), ("work_k3.png", k3, 9), ("work_k4.png", k4, 9), ("work_k5.png", k5, 4)]:
        print(sheet([fn(t) for t in range(n)], name))


# ---------------------------------------------------------------------------------------------------------------- E at rest (the user's pick: E is the working Bob)
def e_base(d, look=0, mood="smile", clip=True, lines=3, top=0, blink=False):
    """E exactly (face 16,12 r7; hat 16,0 r9 brim 11; clipboard 5..27 x 17..31), not writing."""
    face(d, 16, 12 + top, 7, mood, look=look)
    hat(d, 16, 0 + top, 9, brim=11)
    if blink:
        for ex in (16 - 7 // 2 - 1 + look, 16 + 7 // 2 + look):
            d.rectangle([ex, 11 + top, ex + 1, 12 + top], fill=W)
            d.line([ex, 12 + top, ex + 1, 12 + top], fill=K)
    if clip:
        d.rectangle([5, 17, 27, 31], fill=W, outline=K)
        d.rectangle([13, 15, 19, 18], fill=K)
        for i, y in enumerate((21, 24, 27)[:lines]):
            d.line([8, y, 24 - (6 if i == lines - 1 else 0), y], fill=K)


def idle_1(t):   # resting smile, blinks now and then
    im, d = canvas(32, 32); e_base(d, blink=(t == 5)); return onebit(im)


def idle_2(t):   # looks around: left, ahead, right, ahead
    im, d = canvas(32, 32); e_base(d, look=(-1, 0, 0, 1, 1, 0)[t % 6]); return onebit(im)


def idle_3(t):   # done: a big tick on the clipboard, smiling
    im, d = canvas(32, 32); e_base(d, lines=0)
    pts = [(10, 24), (14, 28), (23, 19)]
    n = min(t + 1, 3)
    if n >= 2:
        d.line(pts[:n], fill=K, width=2)
    return onebit(im)


def idle_4(t):   # thumbs up beside the clipboard
    im, d = canvas(32, 32); e_base(d)
    up = (0, -1, -2, -1)[t % 4]
    d.rectangle([26, 19 + up, 30, 24 + up], fill=W, outline=K)                 # fist
    d.rectangle([27, 15 + up, 29, 19 + up], fill=W, outline=K)                 # thumb
    return onebit(im)


def idle_5(t):   # waves the pencil hello
    im, d = canvas(32, 32); e_base(d)
    a = math.radians(-70 + (0, 18, 30, 18)[t % 4])
    x, y = 26, 22
    d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=W, outline=K)
    pencil(d, x + 1, y - 2, ang=-90 + (0, 18, 30, 18)[t % 4] - 20, n=8)
    return onebit(im)


def idle_6(t):   # tips his hat
    im, d = canvas(32, 32); e_base(d, top=0)
    if t % 4 in (1, 2):                                                        # the hat lifts a little
        im2, d2 = canvas(32, 32)
        face(d2, 16, 12, 7, "smile"); hat(d2, 16, -2, 9, brim=11)
        d2.rectangle([5, 17, 27, 31], fill=W, outline=K); d2.rectangle([13, 15, 19, 18], fill=K)
        for i, y in enumerate((21, 24, 27)):
            d2.line([8, y, 24 - (6 if i == 2 else 0), y], fill=K)
        return onebit(im2)
    return onebit(im)


def idle_7(t):   # dozing while waiting: eyes shut, a little z drifts up
    im, d = canvas(32, 32); e_base(d, blink=True, mood="focus")
    zx, zy = 26 + (t % 4), 10 - (t % 4) * 2
    d.line([zx, zy, zx + 3, zy], fill=K); d.line([zx + 3, zy, zx, zy + 3], fill=K); d.line([zx, zy + 3, zx + 3, zy + 3], fill=K)
    return onebit(im)


def idle_8(t):   # whistling: little notes float up
    im, d = canvas(32, 32); e_base(d, mood="focus")
    d.ellipse([15, 16, 17, 18], outline=K)                                     # round mouth
    nx, ny = 24 + (t % 3), 12 - (t % 3) * 3
    d.rectangle([nx, ny, nx + 1, ny + 1], fill=K); d.line([nx + 1, ny - 4, nx + 1, ny], fill=K); d.point((nx + 2, ny - 4), fill=K)
    return onebit(im)


def idle_9(t):   # nods, pleased, holding the finished sheet
    im, d = canvas(32, 32); e_base(d, top=(0, 1, 0, 0)[t % 4])
    return onebit(im)


def idle_10(t):  # winks
    im, d = canvas(32, 32); e_base(d)
    if t % 6 == 3:
        ex = 16 + 7 // 2
        d.rectangle([ex, 11, ex + 1, 12], fill=W); d.line([ex - 1, 12, ex + 2, 12], fill=K)
    return onebit(im)


IDLE = [("idle_1.png", idle_1, 8, "Resting smile, blinks"), ("idle_2.png", idle_2, 6, "Looks around"), ("idle_3.png", idle_3, 4, "Done: big tick"),
        ("idle_4.png", idle_4, 4, "Thumbs up"), ("idle_5.png", idle_5, 4, "Waves the pencil"), ("idle_6.png", idle_6, 4, "Tips his hat"),
        ("idle_7.png", idle_7, 4, "Dozing while waiting (z)"), ("idle_8.png", idle_8, 3, "Whistling"), ("idle_9.png", idle_9, 4, "Nods, pleased"),
        ("idle_10.png", idle_10, 6, "Winks")]
if __name__ == "__main__" and "--idle" in sys.argv:
    for name, fn, n, _ in IDLE:
        print(sheet([fn(t) for t in range(n)], name))


# ---------------------------------------------------------------------------------------------------------------- hello: E's head, greeting (opening screen)
def e_head(d, top=0, look=0, mood="smile", blink=False):
    face(d, 16, 12 + top, 7, mood, look=look)
    hat(d, 16, 0 + top, 9, brim=11)
    if blink:
        for ex in (16 - 7 // 2 - 1 + look, 16 + 7 // 2 + look):
            d.rectangle([ex, 11 + top, ex + 1, 12 + top], fill=W); d.line([ex, 12 + top, ex + 1, 12 + top], fill=K)


def e_body(d, top=20):
    d.rectangle([14, top - 1, 17, top + 1], fill=W, outline=K)                       # neck
    d.rounded_rectangle([9, top, 22, top + 8], radius=3, fill=G, outline=K)          # checked shirt
    d.rounded_rectangle([12, top + 2, 19, top + 8], radius=1, fill=W, outline=K)     # bib
    d.rectangle([10, top + 8, 14, top + 11], fill=K); d.rectangle([17, top + 8, 21, top + 11], fill=K)   # boots


def arm_up(d, ang, sx=22, sy=22, n=9):
    a = math.radians(-30 + ang)                                                   # out to the side, clear of the ear
    hx, hy = sx + n * math.cos(a), sy + n * math.sin(a)
    d.line([sx, sy, hx, hy], fill=K, width=3); d.line([sx, sy, hx, hy], fill=W, width=1)
    d.rectangle([round(hx) - 2, round(hy) - 2, round(hx) + 2, round(hy) + 2], fill=W, outline=K)   # open hand
    d.point((round(hx) - 1, round(hy) - 3), fill=K); d.point((round(hx) + 1, round(hy) - 3), fill=K)  # fingers


def g1(t):   # waves with the right hand, smiling, blinks once
    im, d = canvas(32, 32); e_body(d); d.rectangle([7, 22, 9, 26], fill=W, outline=K)
    arm_up(d, (0, -16, -28, -16)[t % 4]); e_head(d, blink=(t == 5)); return onebit(im)


def g2(t):   # E's pose: peeks over the clipboard and pops a hand up to wave
    im, d = canvas(32, 32); e_base(d)
    up = (0, -3, -5, -3, 0, 0)[t % 6]
    if up:
        x, y = 26, 19 + up
        d.rectangle([x - 2, y - 2, x + 2, y + 2], fill=W, outline=K); d.point((x - 1, y - 3), fill=K); d.point((x + 1, y - 3), fill=K)
    return onebit(im)


def g3(t):   # hooray: both arms up, a little hop
    im, d = canvas(32, 32)
    hop = (0, -1, -2, -1)[t % 4]
    im2, d2 = canvas(32, 32); e_body(d2, top=20 + hop)
    for sx, sgn in ((9, -1), (22, 1)):
        a = math.radians(-90 + sgn * (35 + (0, 6, 10, 6)[t % 4]))
        hx, hy = sx + 7 * math.cos(a), 22 + hop + 7 * math.sin(a)
        d2.line([sx, 22 + hop, hx, hy], fill=K, width=3); d2.line([sx, 22 + hop, hx, hy], fill=W, width=1)
        d2.rectangle([round(hx) - 1, round(hy) - 1, round(hx) + 1, round(hy) + 1], fill=W, outline=K)
    e_head(d2, top=hop)
    return onebit(im2)


def g4(t):   # tips his hat to you
    im, d = canvas(32, 32); e_body(d); d.rectangle([7, 22, 9, 26], fill=W, outline=K)
    lift = (0, -2, -3, -2)[t % 4]
    face(d, 16, 12, 7, "smile")
    hat(d, 16 + (1 if lift else 0), lift, 9, brim=11)
    d.line([22, 22, 25, 8 + lift], fill=K, width=3); d.line([22, 22, 25, 8 + lift], fill=W, width=1)   # arm up to the brim
    d.rectangle([24, 6 + lift, 27, 9 + lift], fill=W, outline=K)
    return onebit(im)


def g5(t):   # waves next to a little speech bubble that says HI
    im, d = canvas(32, 32); e_body(d); d.rectangle([7, 22, 9, 26], fill=W, outline=K)
    arm_up(d, (0, -16, -28, -16)[t % 4], n=6)
    e_head(d)
    if t % 4 != 0:                                                                   # the bubble pops in
        d.rectangle([22, 0, 31, 7], fill=W, outline=K); d.line([24, 7, 22, 10], fill=K)
        d.line([24, 2, 24, 5], fill=K); d.line([26, 2, 26, 5], fill=K); d.line([24, 3, 26, 3], fill=K)   # H
        d.line([28, 2, 28, 5], fill=K)                                               # I
    return onebit(im)


HELLO = [("hello_1.png", g1, 8, "Waves, blinks"), ("hello_2.png", g2, 6, "Peeks over the clipboard, pops a hand up"),
         ("hello_3.png", g3, 4, "Hooray: both arms up, a little hop"), ("hello_4.png", g4, 4, "Tips his hat"),
         ("hello_5.png", g5, 4, "Waves, says HI")]
if __name__ == "__main__" and "--hello" in sys.argv:
    for name, fn, n, _ in HELLO:
        print(sheet([fn(t) for t in range(n)], name))
