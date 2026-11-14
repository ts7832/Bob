"""Metres per pixel of a drawing sheet, found three independent ways and checked against each other.

    scale(texts, width_px, height_px, img=None, dpi=None)
        -> dict(m_per_px, source, estimated, check, disagreement_pct, candidates, denominator, paper, bare_unit, main)
    meters(value_text, unit=None) -> metres, or None if the text is not a length

texts: [{text, bbox: [x0, y0, x1, y1]}] in sheet pixels (OCR of an image, or the text layer of a rendered PDF page).
img:   the sheet image (path, or a numpy array, grey or BGR) - optional; used to put the scale bar's labels on its tick
       marks and to measure lone dimension lines. dpi: pixels per inch of paper when known (a rendered PDF page).

Where the scale comes from, in order of preference:
 1. Scale bar: a row of labels "0 10m 20m" / "0' 2' 4' 8'" / "0 500 1000 2000" next to a bar. Label positions give pixels
    per unit; on the image, the bar's tick marks (vertical edges in the band beside the labels) replace the label centres
    (labels sit up to half a label width off their tick). Survives any rescaling of the image.
 2. Printed scale + paper size: "1:200" / '1/4" = 1'-0"' (converted by pdfsheet.scale_denominator) and the sheet size from
    the title block (A2 under "SIZE", "ISO A1", "ANSI D", "ARCH D", "1:100 (A3)") with the image size. Trusted only when
    the image has that paper's aspect ratio (the image is the whole sheet). A rendered PDF page knows its paper size (dpi).
 3. Dimension strings: in a chain of dimensions the distance between two neighbouring labels' centres is half the sum of
    their values (text only, no image needed); a lone overall dimension is measured along its dimension line on the image.

A bar with bare numbers takes its unit from the printed scale (the unit that makes them agree), else ft on imperial
sheets, else mm for numbers of 100 and more and m below. Bare dimension numbers ("27000") get their unit the same way, on
metric sheets only (on an imperial sheet a bare number is a tag, not a length); `bare_unit` in the result is that unit, to
pass to meters(). `estimated` is True when the chosen value rests on such an assumed unit.

Several views at different scales: every printed scale is a candidate with its position. The main drawing's is the one
the dimension strings (measured on the sheet) or the bar agree with; failing that, the one nearest to most of the text.
"""
import math
import re
from statistics import median

from .pdfsheet import IMPERIAL, SCALE_TXT, scale_denominator

UNIT_M = {"m": 1.0, "mm": 0.001, "cm": 0.01, "km": 1000.0, "ft": 0.3048, "in": 0.0254}

# paper sizes, long x short side in mm
PAPER_MM = {
    "A0": (1189, 841), "A1": (841, 594), "A2": (594, 420), "A3": (420, 297), "A4": (297, 210),
    "B0": (1414, 1000), "B1": (1000, 707), "B2": (707, 500), "B3": (500, 353),
    "ANSI A": (279.4, 215.9), "ANSI B": (431.8, 279.4), "ANSI C": (558.8, 431.8), "ANSI D": (863.6, 558.8),
    "ANSI E": (1117.6, 863.6),
    "ARCH A": (304.8, 228.6), "ARCH B": (457.2, 304.8), "ARCH C": (609.6, 457.2), "ARCH D": (914.4, 609.6),
    "ARCH E": (1219.2, 914.4), "ARCH E1": (1066.8, 762.0),
}
ASPECT_TOL = 0.02     # image aspect vs paper aspect: within this the image is taken to be the whole sheet
AGREE = 0.03          # two sources agree when they differ by less than this
INK = 160             # grey level below which a pixel is ink

# ---------------------------------------------------------------------------------------------------- lengths

_QUOTES = [("’", "'"), ("‘", "'"), ("′", "'"), ("´", "'"), ("`", "'"), ("”", '"'), ("“", '"'), ("″", '"'), ("''", '"')]
_SUFFIX = re.compile(r"\s*\(?\s*(?:typ(?:ical)?|clr|clear|min|max|approx|nom|o\.?c\.?)\.?\s*\)?\s*$", re.I)
_PREFIX = re.compile(r"^\s*(?:±|\+/-|~|approx\.?|ca\.?)\s*", re.I)
_FTIN = re.compile(r"(?:(?P<ft>\d+(?:\.\d+)?)\s*(?:'|ft\.?|feet|foot)\s*-?\s*)?"
                   r"(?:(?P<inch>\d+(?:\.\d+)?)?\s*-?\s*(?:(?P<num>\d+)\s*/\s*(?P<den>\d+))?\s*(?P<mark>\"|in\.?|inch(?:es)?)?)?", re.I)
_NUM = r"\d{1,3}(?:[ ,]\d{3})+|\d+(?:[.,]\d+)?"
_METRIC = re.compile(rf"(?P<n>{_NUM})\s*(?P<u>mm|cm|km|m|metres?|meters?|millimet(?:re|er)s?)\.?", re.I)
_BARE = re.compile(rf"(?P<n>{_NUM})")


def _norm(s):
    for a, b in _QUOTES:
        s = s.replace(a, b)
    return re.sub(r"(?<=\d)\s?(?:\*|°)", '"', s).strip()


def _number(s):
    """'27 000' / '27,000' -> 27000 ; '6,20' -> 6.2 ; '6.20' -> 6.2"""
    if re.fullmatch(r"\d{1,3}(?:[ ,]\d{3})+", s):
        return float(re.sub(r"[ ,]", "", s))
    return float(s.replace(",", "."))


def _unit_name(u):
    u = (u or "").lower().rstrip(".")
    if not u:
        return None
    if u in ("'", "ft", "feet", "foot") or u.startswith("'"):
        return "ft"
    if u in ('"', "in", "inch", "inches"):
        return "in"
    if u.startswith("milli") or u == "mm":
        return "mm"
    if u in ("m", "metre", "metres", "meter", "meters"):
        return "m"
    return u if u in UNIT_M else None


def meters(value_text, unit=None):
    """A written length -> metres. None if it is not one (or a bare number and `unit` is not given).

    323'-4"  52'-8"  5'-6 1/2"  12'  3/4"  -> feet and inches
    6.20 m   620 mm   6,20m                -> metric with its unit
    27000 (unit='mm')  6.20 (unit='m')     -> a bare number in the sheet's dimension unit (scale()['bare_unit'])"""
    if value_text is None:
        return None
    s = _PREFIX.sub("", _SUFFIX.sub("", _norm(str(value_text))))
    if not s or not re.search(r"\d", s):
        return None
    m = _FTIN.fullmatch(s)
    if m:
        ft, inch, num, den, mark = m.group("ft", "inch", "num", "den", "mark")
        has_in = inch is not None or num is not None
        if ft is not None or (has_in and mark):
            if num and int(den) == 0:
                return None
            return float(ft or 0) * 0.3048 + (float(inch or 0) + (int(num) / int(den) if num else 0)) * 0.0254
    m = _METRIC.fullmatch(s)
    if m:
        return _number(m.group("n")) * UNIT_M[_unit_name(m.group("u"))]
    m = _BARE.fullmatch(s)
    if m and unit in UNIT_M:
        return _number(m.group("n")) * UNIT_M[unit]
    return None


# ---------------------------------------------------------------------------------------------------- boxes, image

def _box(t):
    x0, y0, x1, y1 = (float(v) for v in t["bbox"])
    return dict(text=_norm(t.get("text") or ""), x0=x0, y0=y0, x1=x1, y1=y1, xc=(x0 + x1) / 2, yc=(y0 + y1) / 2,
                w=x1 - x0, h=y1 - y0)


def _crop(img, x0, y0, x1, y1):
    """Grey crop (darkest channel of a colour image, so coloured lines count as ink); None if empty."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None, x0, y0
    c = img[y0:y1, x0:x1]
    if c.ndim == 3:
        c = c.min(axis=2)
    return c, x0, y0


# ---------------------------------------------------------------------------------------------------- 1. scale bar

_BAR_TOK = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*(?P<u>'\s*-\s*0\s*\"?|mm|cm|km|metres?|meters?|m|ft\.?|feet|'|\")?(?![A-Za-z0-9])", re.I)
_UNIT_WORD = re.compile(r"(?:m|mm|cm|ft|feet|metres?|meters?|millimet(?:re|er)s?)\.?|(?:scale\s+in\s+)?(?:feet|metres|meters|millimet(?:re|er)s)", re.I)


def _bar_tokens(B):
    """Numeric labels that can sit on a scale bar: '0', '10m', "20'", '500', and several in one box ('0 500 1000 2000')."""
    out = []
    for i, b in enumerate(B):
        s = b["text"]
        if not s or b["w"] <= 0 or b["h"] <= 0 or (b["h"] > 1.5 * b["w"] and len(s) >= 3):
            continue  # vertical text: bars are horizontal
        toks = list(_BAR_TOK.finditer(s))
        if not toks or _BAR_TOK.sub("", s).strip(" ,;|") != "":
            continue
        if any(len(m.group("n")) > 1 and m.group("n")[0] == "0" and not m.group("n").startswith("0.") for m in toks):
            continue  # '009', '00': not a bar label (often a rotated '600' read backwards)
        for m in toks:
            if len(toks) == 1:
                xc, w = b["xc"], b["w"]
            else:  # several labels in one box: place each by its characters
                xc = b["x0"] + b["w"] * (m.start() + m.end()) / 2 / len(s)
                w = b["w"] * (m.end() - m.start()) / len(s)
            out.append(dict(v=float(m.group("n")), unit=_unit_name(m.group("u")), xc=xc, yc=b["yc"], h=b["h"], w=w,
                            x0=xc - w / 2, x1=xc + w / 2, y0=b["y0"], y1=b["y1"], i=i, text=m.group(0).strip()))
    return out


def _fit(vs, xs):
    n = len(vs)
    mv, mx = sum(vs) / n, sum(xs) / n
    sv = sum((v - mv) ** 2 for v in vs)
    if sv == 0:
        return None, None, None
    b = sum((v - mv) * (x - mx) for v, x in zip(vs, xs)) / sv
    a = mx - b * mv
    return a, b, max(abs(x - (a + b * v)) for v, x in zip(vs, xs))


def _rows(toks, max_gap_h=30):
    """Group label tokens that sit on one text line (same height, same baseline band, not too far apart)."""
    toks = sorted(toks, key=lambda t: t["xc"])
    parent = list(range(len(toks)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(toks):
        for j in range(i + 1, len(toks)):
            b = toks[j]
            hh = max(a["h"], b["h"])
            if b["xc"] - a["xc"] > max_gap_h * hh:
                break
            if abs(a["yc"] - b["yc"]) <= 0.4 * hh and hh / max(1e-6, min(a["h"], b["h"])) <= 1.5:
                parent[find(i)] = find(j)
    groups = {}
    for i, t in enumerate(toks):
        groups.setdefault(find(i), []).append(t)
    return [sorted(g, key=lambda t: t["xc"]) for g in groups.values()]


def _bar_runs(row):
    """Consecutive labels whose values increase left to right, each run a possible scale bar."""
    runs, cur = [], [row[0]]
    for t in row[1:]:
        if t["v"] > cur[-1]["v"] and t["xc"] - cur[-1]["xc"] > 0.5 * t["h"]:
            cur.append(t)
        else:
            runs.append(cur)
            cur = [t]
    runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def _is_bar(run):
    vals = [t["v"] for t in run]
    units = {t["unit"] for t in run if t["unit"]}
    if len(units) > 1:
        return False, "labels in different units"
    has0 = vals[0] == 0
    n_unit = sum(1 for t in run if t["unit"])
    if not has0 and (n_unit < 3 or len(run) < 3):
        return False, "no 0 and fewer than three labels with units: grid/border numbers or pipe sizes, not a bar"
    if len(run) == 2 and not n_unit:
        return False, "two bare numbers"
    if len(run) >= 3:
        a, b, res = _fit(vals, [t["xc"] for t in run])
        if b is None or b <= 0 or res > 0.04 * (run[-1]["xc"] - run[0]["xc"]):
            return False, "label positions not proportional to their values"
    return True, ""


def _ticks(img, run, B):
    """Positions of the bar's tick marks next to the labels: vertical edges in a band just above or below the labels
    (other text masked out). Each label snaps to the edge nearest its centre; returns the side with most snaps."""
    import numpy as np
    h = median(t["h"] for t in run)
    xa, xb = run[0]["x0"] - 2 * h, run[-1]["x1"] + 2 * h
    top, bot = min(t["y0"] for t in run), max(t["y1"] for t in run)
    gaps = [b["xc"] - a["xc"] for a, b in zip(run, run[1:])]
    best = None
    for side, (ya, yb) in (("above", (top - 2.5 * h, top - 0.05 * h)), ("below", (bot + 0.05 * h, bot + 2.5 * h))):
        crop, ox, oy = _crop(img, xa, ya, xb, yb)
        if crop is None:
            continue
        ink = crop < INK
        pad = 0.15 * h
        for b in B:  # text is not bar
            if b["x1"] + pad < ox or b["x0"] - pad > ox + ink.shape[1] or b["y1"] + pad < oy or b["y0"] - pad > oy + ink.shape[0]:
                continue
            ink[max(0, int(b["y0"] - pad - oy)):max(0, int(b["y1"] + pad - oy)),
                max(0, int(b["x0"] - pad - ox)):max(0, int(b["x1"] + pad - ox))] = False
        # the bar itself: a row with ink along most of the labelled span
        span0, span1 = int(run[0]["xc"] - ox), int(run[-1]["xc"] - ox)
        if span1 - span0 < 4 or ink[:, max(0, span0):span1].mean(axis=1).max() < 0.6:
            continue
        edge = (ink[:, 1:] != ink[:, :-1]).sum(axis=0).astype(float)
        if edge.max() <= 0:
            continue
        thr = max(0.3 * edge.max(), 0.15 * h)
        cols = np.nonzero(edge >= thr)[0]
        peaks, grp = [], []
        for c in cols:  # merge the two edges of one line (and a few px of jitter) into one tick
            if grp and c - grp[-1] > max(3, 0.12 * h):
                peaks.append(sum(g * edge[g] for g in grp) / sum(edge[g] for g in grp))
                grp = []
            grp.append(c)
        if grp:
            peaks.append(sum(g * edge[g] for g in grp) / sum(edge[g] for g in grp))
        peaks = [float(p) + 0.5 + ox for p in peaks]
        snapped = []
        for k, t in enumerate(run):
            near = [g for g in (gaps[k - 1] if k > 0 else None, gaps[k] if k < len(gaps) else None) if g]
            tol = min(0.4 * min(near), 0.75 * t["w"] + 0.3 * h)
            p = min(peaks, key=lambda p: abs(p - t["xc"]), default=None)
            if p is not None and abs(p - t["xc"]) <= tol:
                snapped.append((t["v"], p, t["xc"]))
        if len(snapped) >= 2:
            a, b, res = _fit([s[0] for s in snapped], [s[1] for s in snapped])
            if len(snapped) >= 3 and res > 0.01 * (snapped[-1][1] - snapped[0][1]):  # one tick off: drop the worst once
                worst = max(snapped, key=lambda s: abs(s[1] - (a + b * s[0])))
                snapped.remove(worst)
                a, b, res = _fit([s[0] for s in snapped], [s[1] for s in snapped])
            if b and b > 0:
                cand = dict(side=side, snapped=snapped, a=float(a), b=float(b), res=float(res))
                if best is None or (len(snapped), -res) > (len(best["snapped"]), -best["res"]):
                    best = cand
    return best


def _bars(B, img=None):
    toks = _bar_tokens(B)
    out = []
    for row in _rows(toks):
        for run in _bar_runs(row):
            ok, why = _is_bar(run)
            # OCR often loses the '0': two labels in m or ft ('10m 20m') count when the image shows a bar under both
            two = (len(run) == 2 and run[0]["unit"] == run[1]["unit"] and run[0]["unit"] in ("m", "ft") and img is not None)
            if not ok and not two:
                continue
            if not ok:
                tk = _ticks(img, run, B)
                if not tk or len(tk["snapped"]) < 2:
                    continue
            vals, xs = [t["v"] for t in run], [t["xc"] for t in run]
            a, b, res = _fit(vals, xs)
            if b is None or b <= 0:
                continue
            unit = next((t["unit"] for t in run if t["unit"]), None)
            unit_from = "its labels" if unit else None
            h = median(t["h"] for t in run)
            if not unit:  # a unit word next to the bar: "m", "METRES", "SCALE IN FEET"
                for c in B:
                    if c["xc"] < run[0]["x0"] - 3 * h or c["xc"] > run[-1]["x1"] + 4 * h or abs(c["yc"] - run[-1]["yc"]) > 3 * h:
                        continue
                    if _UNIT_WORD.fullmatch(c["text"].strip()) or re.search(r"\b(feet|metres|meters)\b", c["text"], re.I):
                        unit = _unit_name(re.search(r"mm|cm|ft|feet|millimet|metre|meter|m", c["text"], re.I).group(0).lower()
                                          .replace("feet", "ft").replace("millimet", "mm").replace("metre", "m").replace("meter", "m"))
                        unit_from = f"the word '{c['text']}' next to it"
                        break
            bar = dict(kind="bar", labels=[t["text"] for t in run], values=vals, unit=unit, unit_from=unit_from,
                       px_per_unit=b, label_residual_px=round(res, 1), ticks=False,
                       bbox=[round(min(t["x0"] for t in run)), round(min(t["y0"] for t in run)),
                             round(max(t["x1"] for t in run)), round(max(t["y1"] for t in run))],
                       text_index=sorted({t["i"] for t in run}))
            if img is not None:
                tk = _ticks(img, run, B)
                if tk:
                    bar.update(px_per_unit=tk["b"], ticks=True, tick_side=tk["side"], ticks_used=len(tk["snapped"]),
                               tick_residual_px=round(tk["res"], 1),
                               label_vs_tick_pct=round(100 * (b / tk["b"] - 1), 2),
                               zero_x=round(tk["a"], 1))
            bar["span_px"] = round(bar["px_per_unit"] * (vals[-1] - vals[0]), 1)
            out.append(bar)
    return out


# ---------------------------------------------------------------------------------------------------- 2. printed scale

_SCALE_WORDS = re.compile(r"scale|ma(?:ss|ß)stab|[ée]chelle|escala|scala|skala|schaal|m[åa]lestok|m[õo]{2}tkava|"
                          r"mittakaava|m[ěe][řr][íi]tko|skaala", re.I)
_RATIO = re.compile(r"(?<![\d.,:/])1\s*:\s*(\d{1,5}(?:[.,]\d+)?)(?![\d:/])")
_NTS = re.compile(r"\bN\.?\s?T\.?\s?S\.?\b|not\s+to\s+scale", re.I)
_AS_IND = re.compile(r"\bas\s+(?:indicated|shown|noted)\b", re.I)
_PAPER_INLINE = re.compile(r"(?:@|\bat\b|\(|\bon\b)\s*(?:ISO\s*)?(A[0-4]|B[0-3]|ANSI\s*[A-E]|ARCH\s*(?:E1|[A-E]))\b", re.I)
_COMMON_DEN = {1, 2, 2.5, 5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 750, 1000, 1250, 1500, 2000,
               2500, 5000, 10000}


def _paper_key(s):
    s = re.sub(r"\s+", " ", s.upper().replace("ISO", "").replace(".", "")).strip()
    m = re.fullmatch(r"(ANSI|ARCH) ?(E1|[A-E])", s)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return s if s in PAPER_MM else None


def _note(s):
    """One text -> (kind, denominator, inline paper, (start, end) of the scale in it) or None.
    kind: ratio / imperial / nts / as-indicated."""
    if _NTS.search(s):
        return "nts", None, None, (0, len(s))
    if _AS_IND.search(s) and not _RATIO.search(s) and not IMPERIAL.search(s):
        return "as-indicated", None, None, (0, len(s))
    pap = _PAPER_INLINE.search(s)
    pap_end = pap.end() if pap else 0
    pap = _paper_key(pap.group(1)) if pap else None
    m = IMPERIAL.search(s)
    if m:  # imperial first: SCALE_TXT would read 'SCALE: 1/4" = 1'-0"' as 1:4
        return "imperial", scale_denominator(m.group(0)), pap, (m.start(), m.end())
    m = _RATIO.search(s)
    if m:
        rest = (s[:m.start()] + s[m.end():])
        rest = _PAPER_INLINE.sub("", rest)
        den = float(m.group(1).replace(",", "."))
        if _SCALE_WORDS.search(s) or (re.fullmatch(r"[\s:=@()\-–,.]*", rest) and den in _COMMON_DEN):
            return "ratio", den, pap, (m.start(), max(m.end(), pap_end))
        return None
    if SCALE_TXT.search(s) and re.match(r"\s*m\b", s, re.I):  # 'M 1:100'
        m = SCALE_TXT.search(s)
        return "ratio", scale_denominator(s), pap, (m.start(), m.end())
    return None


def _short(s, span, limit=40):
    """The scale part of a long title-block line: 'Mõõtkava 1:100 · lõige 1.20 m ...' -> 'Mõõtkava 1:100'."""
    if len(s) <= limit:
        return s
    a, b = span
    w = [m for m in _SCALE_WORDS.finditer(s) if m.end() <= a and a - m.start() <= 25]
    return s[w[-1].start() if w else a:b].strip()


def _notes(B):
    out = []
    for i, b in enumerate(B):
        n = _note(b["text"])
        if n and (n[1] is None or 1 <= n[1] <= 20000):
            out.append(dict(kind="note", note=n[0], denominator=n[1], text=_short(b["text"], n[3]), paper_inline=n[2],
                            bbox=[round(b["x0"]), round(b["y0"]), round(b["x1"]), round(b["y1"])], text_index=i))
    return out


_PAPER_TXT = re.compile(r"\b(?:ISO\s*)?(A[0-4]|B[0-3])\b|\b(ANSI)\s*-?\s*([A-E])\b|\b(ARCH)\.?\s*-?\s*(E1|[A-E])\b", re.I)
_SIZE_WORD = re.compile(r"\b(?:size|paper|format|formaatti|blattgr(?:ö|oe)sse|koko|formato)\b", re.I)
_INCH_SIZE = re.compile(r"\b(\d{2})\s*\"?\s*[xX×]\s*(\d{2})\s*\"?")
_INCH_SIZES = {(12, 9): "ARCH A", (18, 12): "ARCH B", (24, 18): "ARCH C", (36, 24): "ARCH D", (48, 36): "ARCH E",
               (42, 30): "ARCH E1", (11, 8.5): "ANSI A", (17, 11): "ANSI B", (22, 17): "ANSI C", (34, 22): "ANSI D",
               (44, 34): "ANSI E"}


def _papers(B):
    """Sheet sizes printed in the title block. A bare 'A1' is often a view or detail reference, so it counts only with a
    prefix (ISO A1, ANSI D, ARCH D), inside a box that says SIZE/FORMAT, or right next to such a box."""
    size_boxes = [b for b in B if _SIZE_WORD.search(b["text"])]
    out = []
    for i, b in enumerate(B):
        s = b["text"]
        for m in _PAPER_TXT.finditer(s):
            key = _paper_key(m.group(0))
            if not key:
                continue
            prefixed = bool(re.match(r"(ISO|ANSI|ARCH)", m.group(0), re.I))
            how = None
            if prefixed:
                how = f"'{s}'"
            elif _SIZE_WORD.search(s):
                how = f"'{s}'"
            else:
                for sb in size_boxes:
                    hh = max(b["h"], sb["h"])
                    if math.hypot(sb["xc"] - b["xc"], sb["yc"] - b["yc"]) <= 4 * hh and (sb["yc"] < b["yc"] + 0.5 * hh or sb["xc"] < b["xc"]):
                        how = f"'{s}' next to '{sb['text']}'"
                        break
            if how:
                out.append(dict(paper=key, how=how, bbox=[round(b["x0"]), round(b["y0"]), round(b["x1"]), round(b["y1"])]))
        if _SIZE_WORD.search(s):
            m = _INCH_SIZE.search(s)
            if m:
                k = tuple(sorted((int(m.group(1)), int(m.group(2))), reverse=True))
                if k in _INCH_SIZES:
                    out.append(dict(paper=_INCH_SIZES[k], how=f"'{s}'", bbox=[round(b["x0"]), round(b["y0"]), round(b["x1"]), round(b["y1"])]))
    return out


def _paper_fit(key, W, H):
    """mm of paper per image pixel if the image is this whole paper (aspect within ASPECT_TOL), else (None, aspect error)."""
    L, S = PAPER_MM[key]
    err = (max(W, H) / min(W, H)) / (L / S) - 1
    if abs(err) > ASPECT_TOL:
        return None, err
    return (L / max(W, H) + S / min(W, H)) / 2, err


def _implied_paper(mm_per_px, W, H):
    """Which standard paper is the image, if one image pixel is mm_per_px of paper? -> (name, % off) or (None, None)."""
    L, S = max(W, H) * mm_per_px, min(W, H) * mm_per_px
    best = min(PAPER_MM, key=lambda k: abs(math.log(L / PAPER_MM[k][0])) + abs(math.log(S / PAPER_MM[k][1])))
    off = 100 * (L / PAPER_MM[best][0] - 1)
    return (best, off) if abs(off) <= 2 and abs(S / PAPER_MM[best][1] - 1) <= 0.03 else (None, None)


# ---------------------------------------------------------------------------------------------------- 3. dimension strings

def _dim_tokens(B, bare_unit):
    out = []
    for i, b in enumerate(B):
        s = b["text"]
        if len(s) < 2 or not re.search(r"\d", s):
            continue
        bare = bool(_BARE.fullmatch(_PREFIX.sub("", _SUFFIX.sub("", s))))
        if bare:
            if bare_unit is None or (len(s) > 1 and s[0] == "0" and not s.startswith("0.")):
                continue
            if bare_unit == "mm" and (not s.isdigit() or int(s) < 50):
                continue  # small integers are tags and bubble numbers, not mm
            if bare_unit == "m" and not re.fullmatch(r"\d+[.,]\d{2}", s):
                continue
        elif not re.search(r"['\"]|\d\s*(mm|cm|m)\b", s, re.I):
            continue
        v = meters(s, unit=bare_unit if bare else None)
        if not v or v <= 0:
            continue
        vertical = b["h"] > 1.3 * b["w"]
        out.append(dict(i=i, text=s, m=v, bare=bare, vertical=vertical,
                        along=b["yc"] if vertical else b["xc"], across=b["xc"] if vertical else b["yc"],
                        thick=b["w"] if vertical else b["h"], length=b["h"] if vertical else b["w"], box=b))
    return out


def _chains(toks):
    """Neighbouring labels on one dimension line: centre distance = (L1 + L2) / 2 -> px per metre for each pair.
    Returns the value most pairs agree on (within 1.5%) and how many do."""
    pairs = []
    for vertical in (False, True):
        T = sorted((t for t in toks if t["vertical"] == vertical), key=lambda t: t["along"])
        rows = []
        for t in T:  # same line: same position across, same text height
            for r in rows:
                q = r[-1]
                if abs(q["across"] - t["across"]) <= 0.35 * max(q["thick"], t["thick"]) and max(q["thick"], t["thick"]) / min(q["thick"], t["thick"]) <= 1.35:
                    r.append(t)
                    break
            else:
                rows.append([t])
        for r in rows:
            r.sort(key=lambda t: t["along"])
            for a, b in zip(r, r[1:]):
                d = b["along"] - a["along"]
                if d <= (a["length"] + b["length"]) / 2:
                    continue
                pairs.append(dict(px_per_m=d / ((a["m"] + b["m"]) / 2), a=a["text"], b=b["text"], ia=a["i"], ib=b["i"],
                                  vertical=vertical, across=a["across"]))
    if not pairs:
        return None, pairs
    L = [math.log(p["px_per_m"]) for p in pairs]
    best = max(range(len(L)), key=lambda i: sum(1 for x in L if abs(x - L[i]) <= 0.015))
    c = median(x for x in L if abs(x - L[best]) <= 0.015)
    agree = [p for p, x in zip(pairs, L) if abs(x - c) <= 0.015]
    return dict(px_per_m=math.exp(c), agree=agree, n_pairs=len(pairs)), pairs


def _dim_line(img, t, expect_px):
    """Length in px of the dimension line under/over/through a lone dimension label, ended at its extension lines.
    None when no line of roughly the expected length passes the label."""
    import numpy as np
    b, th = t["box"], t["thick"]
    if t["vertical"]:
        crop, ox, oy = _crop(img, b["x0"] - 1.5 * th, b["yc"] - 0.75 * expect_px, b["x1"] + 1.5 * th, b["yc"] + 0.75 * expect_px)
        if crop is None:
            return None
        ink = (crop < INK).T  # rows = across, columns = along
        lo, hi = b["y0"] - oy, b["y1"] - oy
    else:
        crop, ox, oy = _crop(img, b["xc"] - 0.75 * expect_px, b["y0"] - 1.5 * th, b["xc"] + 0.75 * expect_px, b["y1"] + 1.5 * th)
        if crop is None:
            return None
        ink = crop < INK
        lo, hi = b["x0"] - ox, b["x1"] - ox
    n = ink.shape[1]
    gap = max(3, int(0.1 * th))
    best = None
    for r in range(ink.shape[0]):
        row = ink[r]
        if row.sum() < 0.5 * expect_px:
            continue
        # runs of ink with small gaps bridged
        idx = np.nonzero(row)[0]
        runs, s0, prev = [], idx[0], idx[0]
        for c in idx[1:]:
            if c - prev > gap:
                runs.append((s0, prev))
                s0 = c
            prev = c
        runs.append((s0, prev))
        # the line passes under/over the label, or is broken by it
        cover = [ru for ru in runs if ru[0] <= lo and ru[1] >= hi]
        if cover:
            s, e = cover[0]
        else:
            left = [ru for ru in runs if ru[1] < lo and lo - ru[1] <= 2 * th]
            right = [ru for ru in runs if ru[0] > hi and ru[0] - hi <= 2 * th]
            if not left or not right:
                continue
            s, e = left[-1][0], right[0][1]
        L = e - s
        if 0.7 * expect_px <= L <= 1.4 * expect_px and (best is None or L > best[2]):
            best = (r, s, L)
    if best is None:
        return None
    r, s, L = best
    e = s + L
    # extension lines: the longest ink column crossing the line near each end (ticks and arrows are short)
    def ext(c0, c1):
        top, bot = max(0, r - int(3 * th)), min(ink.shape[0], r + int(3 * th) + 1)
        sc = None
        for c in range(max(0, c0), min(n, c1)):
            col = ink[top:bot, c]
            k = int(col.sum())
            if k >= 1.5 * th * 0.8 and (sc is None or k > sc[1]):
                sc = (c, k)
        return sc[0] if sc else None
    w = max(4, int(0.03 * L))
    cs, ce = ext(s, s + w), ext(e - w, e + 1)
    L2 = (ce if ce is not None else e) - (cs if cs is not None else s)
    return dict(length_px=float(L2), raw_px=float(L), ends="extension lines" if cs is not None and ce is not None else "line ends")


# ---------------------------------------------------------------------------------------------------- the whole sheet

def _pct(a, b):
    return 100 * (a / b - 1)


def _nearest_unit(m_per_unit, allowed=("m", "mm", "cm", "ft", "in")):
    u = min(allowed, key=lambda u: abs(math.log(m_per_unit / UNIT_M[u])))
    return u if abs(math.log(m_per_unit / UNIT_M[u])) <= math.log(1.25) else None


def scale(texts, width_px, height_px, img=None, dpi=None):
    """Metres per pixel of this sheet, where it came from, and how well the independent sources agree. See module doc."""
    W, H = float(width_px), float(height_px)
    own = isinstance(img, str)
    if own:
        import cv2
        img = cv2.imread(img, cv2.IMREAD_GRAYSCALE)
    try:
        return _scale(texts, W, H, img, dpi)
    finally:
        if own:
            del img


def _scale(texts, W, H, img, dpi):
    B = [_box(t) for t in texts if t.get("bbox") and t.get("text")]
    cands = []

    # --- printed scales and paper
    notes = _notes(B)
    papers = _papers(B)
    fits = {}
    for p in papers:
        mmpp, err = _paper_fit(p["paper"], W, H)
        p["aspect_off_pct"] = round(100 * err, 1)
        p["whole_sheet"] = mmpp is not None
        if mmpp:
            fits.setdefault(p["paper"], mmpp)
    page_mmpp = 25.4 / dpi if dpi else None
    sheet_paper = next(iter(fits), None)
    imperial = any(n["note"] == "imperial" for n in notes) or any(re.search(r"\d'\s*-?\s*\d+\"", b["text"]) for b in B)

    for n in notes:
        if n["denominator"] is None:
            continue
        pap, mmpp, how = None, None, None
        if n["paper_inline"]:
            mmpp, err = _paper_fit(n["paper_inline"], W, H)
            pap, how = n["paper_inline"], f"its own '{n['paper_inline']}'" + ("" if mmpp else f" (but the image is not that paper's shape: aspect off by {100 * err:.0f}%)")
        if mmpp is None and sheet_paper:
            pap, mmpp, how = sheet_paper, fits[sheet_paper], f"sheet size {sheet_paper} from the title block"
        if mmpp is None and page_mmpp:
            mmpp, how = page_mmpp, f"the page size ({dpi:g} px per inch of paper)"
            imp, off = _implied_paper(page_mmpp, W, H)
            pap = imp
        n["paper"], n["paper_from"] = pap, how
        n["m_per_px"] = n["denominator"] * mmpp / 1000 if mmpp else None

    # --- scale bars
    bars = _bars(B, img)

    # main printed scale: territory = share of the sheet's text nearest to it
    dens = [n for n in notes if n["denominator"]]
    for n in dens:
        n["territory"] = 0
    if dens:
        for b in B:
            nn = min(dens, key=lambda n: math.hypot(b["xc"] - (n["bbox"][0] + n["bbox"][2]) / 2, b["yc"] - (n["bbox"][1] + n["bbox"][3]) / 2))
            nn["territory"] += 1

    # --- units of bare bar labels
    ref_note = max((n for n in dens if n.get("m_per_px")), key=lambda n: n["territory"], default=None)
    for bar in bars:
        if bar["unit"]:
            continue
        u = None
        if ref_note:
            agreeing = [n for n in dens if n.get("m_per_px") and _nearest_unit(n["m_per_px"] * bar["px_per_unit"])]
            if agreeing:
                n = min(agreeing, key=lambda n: abs(math.log(n["m_per_px"] * bar["px_per_unit"] / UNIT_M[_nearest_unit(n["m_per_px"] * bar["px_per_unit"])])))
                u = _nearest_unit(n["m_per_px"] * bar["px_per_unit"])
                bar["unit_from"] = f"the printed scale '{n['text']}' (the unit that makes them agree)"
        if not u:
            u = "ft" if imperial else ("mm" if max(bar["values"]) >= 100 else "m")
            bar["unit_from"] = "assumed: " + ("imperial sheet" if imperial else "bare numbers of 100 and more are mm" if u == "mm" else "small bare numbers are m")
        bar["unit"] = u
    for bar in bars:
        bar["m_per_px"] = UNIT_M[bar["unit"]] / bar["px_per_unit"]
        bar["unit_assumed"] = (bar.get("unit_from") or "").startswith("assumed")

    # --- dimension chains: explicit units (feet-inches, '6.20 m') and bare numbers in the sheet's dimension unit
    explicit, _ = _chains(_dim_tokens(B, None))
    bare_unit, bare_from = None, None
    if not imperial:
        # bare numbers' unit: decided by whichever measured scale exists, else by their look
        probe = {}
        for u in ("mm", "m"):
            r, _ = _chains([t for t in _dim_tokens(B, u) if t["bare"]])
            if r and len(r["agree"]) >= 2:
                probe[u] = r
        ref = next((b["m_per_px"] for b in bars if not b["unit_assumed"]), None) or (ref_note or {}).get("m_per_px")
        if ref and probe:
            for u, r in probe.items():
                k = r["px_per_m"] * UNIT_M[u]  # px per bare unit
                uu = _nearest_unit(k * ref, ("mm", "cm", "m"))
                if uu:
                    bare_unit, bare_from = uu, "agrees with the bar / printed scale in that unit"
                    break
        if not bare_unit and probe:
            bare_unit = max(probe, key=lambda u: len(probe[u]["agree"]))
            bare_from = "assumed from how the numbers look (integers of 50 and more: mm; two decimals: m)"
    chain = explicit
    if bare_unit and not imperial:
        rb, _ = _chains([t for t in _dim_tokens(B, bare_unit) if t["bare"]])
        if rb and (not chain or len(rb["agree"]) > len(chain["agree"])):
            chain = rb
    dims = None
    if chain and len(chain["agree"]) >= 3 and len(chain["agree"]) >= 0.25 * chain["n_pairs"]:
        dims = dict(kind="dimension chains", m_per_px=1 / chain["px_per_m"], pairs_agree=len(chain["agree"]),
                    pairs_total=chain["n_pairs"], examples=[f"{p['a']} | {p['b']}" for p in chain["agree"][:4]],
                    bare_unit=bare_unit if chain is not explicit else None)

    # --- choose the main scale
    main_bar, main_note, why_note = None, None, None
    trusted_notes = [n for n in dens if n.get("m_per_px")]
    meas = dims["m_per_px"] if dims else None
    if trusted_notes:
        if meas:
            ok = [n for n in trusted_notes if abs(_pct(n["m_per_px"], meas)) <= 100 * AGREE]
            if ok:
                main_note = max(ok, key=lambda n: n["territory"])
                why_note = f"the dimension strings agree with it ({dims['pairs_agree']} pairs)"
        if not main_note and bars:
            ok = [(n, b) for n in trusted_notes for b in bars if abs(_pct(n["m_per_px"], b["m_per_px"])) <= 100 * AGREE]
            if ok:
                main_note = max((n for n, _ in ok), key=lambda n: n["territory"])
                why_note = "the scale bar agrees with it"
    if not main_note and dens:
        main_note = max(dens, key=lambda n: n["territory"])
        why_note = (f"nearest printed scale to {main_note['territory']} of {len(B)} text boxes (the largest view)"
                    if len({n['denominator'] for n in dens}) > 1 else "the only printed scale")
    if bars:
        def bar_score(b):
            s = 0
            if main_note and main_note.get("m_per_px") and abs(_pct(b["m_per_px"], main_note["m_per_px"])) <= 100 * AGREE:
                s += 4
            if meas and abs(_pct(b["m_per_px"], meas)) <= 100 * AGREE:
                s += 4
            s += 2 * b["ticks"] + (0 if b["unit_assumed"] else 1) + 0.1 * len(b["values"])
            if main_note:
                nb = main_note["bbox"]
                s -= 0.5 * math.hypot((b["bbox"][0] + b["bbox"][2] - nb[0] - nb[2]) / 2, (b["bbox"][1] + b["bbox"][3] - nb[1] - nb[3]) / 2) / max(W, H)
            return s
        main_bar = max(bars, key=bar_score)

    # --- lone dimension lines measured on the image (a check, needs a first scale to find the lone overall dims)
    first = (main_bar or {}).get("m_per_px") or (main_note or {}).get("m_per_px") or meas
    lines = []
    if img is not None and first:
        toks = _dim_tokens(B, bare_unit) if bare_unit else _dim_tokens(B, None)
        chained = {i for q in (chain["agree"] if dims else []) for i in (q["ia"], q["ib"])}
        for t in sorted(toks, key=lambda t: -t["m"]):
            if len(lines) >= 3:
                break
            exp = t["m"] / first
            if t["i"] in chained or exp < 12 * t["thick"]:
                continue
            others = [o for o in toks if o is not t and o["vertical"] == t["vertical"] and abs(o["across"] - t["across"]) <= 0.5 * t["thick"]
                      and abs(o["along"] - t["along"]) < 0.5 * exp]
            if others:
                continue  # part of a chain: its line runs on past this segment
            r = _dim_line(img, t, exp)
            if r and r["ends"] == "extension lines":  # a line that does not end at extension lines may be any line
                lines.append(dict(kind="dimension line", text=t["text"], value_m=round(t["m"], 4), length_px=round(r["length_px"], 1),
                                  ends=r["ends"], m_per_px=t["m"] / r["length_px"],
                                  bbox=[round(t["box"]["x0"]), round(t["box"]["y0"]), round(t["box"]["x1"]), round(t["box"]["y1"])]))

    # --- decide, cross-check, explain
    m_per_px, source, picked = None, None, None
    others = []  # (label, value)
    note_txt = (lambda n: f"printed scale '{n['text']}' on {n['paper_from']}") if main_note else None
    if main_bar:
        picked = main_bar
        source = (f"scale bar {' '.join(main_bar['labels'])} ({main_bar['unit']}, unit from {main_bar['unit_from']})"
                  + (f", {main_bar['ticks_used']} tick marks measured on the image" if main_bar["ticks"] else ", label positions (no image to find its ticks)"))
        # a bar that disagrees with both the printed scale and the dimensions, while those two agree: probably another view's
        if main_note and main_note.get("m_per_px") and meas and abs(_pct(main_note["m_per_px"], meas)) <= 100 * AGREE \
                and abs(_pct(main_bar["m_per_px"], meas)) > 100 * AGREE:
            picked = main_note
            source = f"{note_txt(main_note)} (the scale bar {' '.join(main_bar['labels'])} disagrees with it and with the dimension strings)"
    elif main_note and main_note.get("m_per_px"):
        picked, source = main_note, note_txt(main_note)
    elif dims:
        picked = dims
        source = f"dimension strings: {dims['pairs_agree']} neighbouring pairs agree ({', '.join(dims['examples'][:2])})" + \
                 (f"; bare numbers taken as {bare_unit} ({bare_from})" if dims.get("bare_unit") else "")
    if picked:
        m_per_px = picked["m_per_px"]
    # estimated, not measured: the length unit behind the chosen value was assumed rather than read
    estimated = bool(picked and ((picked is main_bar and main_bar["unit_assumed"])
                                 or (picked is dims and dims.get("bare_unit") and (bare_from or "").startswith("assumed"))))

    checks = []
    if picked is not main_bar and main_bar:
        checks.append((f"scale bar {' '.join(main_bar['labels'])}", main_bar["m_per_px"]))
    if main_note and main_note.get("m_per_px") and picked is not main_note:
        checks.append((note_txt(main_note), main_note["m_per_px"]))
    if dims and picked is not dims:
        checks.append((f"{dims['pairs_agree']} dimension-chain pairs", dims["m_per_px"]))
    for l in lines:
        checks.append((f"dimension {l['text']} measured along its line ({l['length_px']:.0f} px to its {l['ends']})", l["m_per_px"]))
    parts, worst = [], None
    if m_per_px:
        for label, v in checks:
            d = _pct(v, m_per_px)
            worst = d if worst is None or abs(d) > abs(worst) else worst
            parts.append(f"{label}: {v:.6g} m/px, {d:+.1f}%")
    # printed scale without a usable paper size: say which paper would make it agree
    no_paper = ("no sheet size printed" if not papers else
                f"printed sheet size {papers[0]['paper']} does not fit the image's shape (aspect off by {papers[0]['aspect_off_pct']:+.1f}%), "
                f"so the image is not the whole sheet")
    if main_note and not main_note.get("m_per_px") and m_per_px and main_note["denominator"]:
        imp, off = _implied_paper(m_per_px * 1000 / main_note["denominator"], W, H)
        pw = max(W, H) * m_per_px * 1000 / main_note["denominator"]
        parts.append(f"printed scale '{main_note['text']}' cannot be checked directly ({no_paper}): it agrees only if the image is "
                     f"{pw:.0f} mm ({pw / 25.4:.1f} in) wide" + (f" = {imp} ({off:+.1f}%)" if imp else ", not a standard paper size")
                     + f", {max(W, H) / (pw / 25.4):.0f} px per inch")
    if main_note and len({n["denominator"] for n in dens}) > 1:
        parts.append(f"several printed scales ({', '.join(sorted({n['text'] for n in dens}))}); main drawing's: '{main_note['text']}' because {why_note}")
    if not m_per_px:
        if main_note and main_note["denominator"]:
            source = f"printed scale '{main_note['text']}' only, and {no_paper}: the image's pixels per inch of paper are unknown"
            alts = []
            for k, (L, S) in PAPER_MM.items():
                mmpp, err = _paper_fit(k, W, H)
                if mmpp:
                    alts.append(dict(kind="note, assumed paper", paper=k, m_per_px=main_note["denominator"] * mmpp / 1000,
                                     dpi=round(25.4 / mmpp)))
            cands += alts
        else:
            source = "no scale bar, printed scale or dimension strings found"

    for n in notes:
        cands.append({k: v for k, v in n.items() if k != "text_index"})
    for b in bars:
        cands.append({k: v for k, v in b.items() if k != "text_index"})
    if dims:
        cands.append(dims)
    cands += lines
    for p in papers:
        cands.append(dict(kind="paper", **p))
    for c in cands:
        if c.get("m_per_px"):
            c["m_per_px"] = float(f"{c['m_per_px']:.6g}")
    return dict(m_per_px=float(f"{m_per_px:.6g}") if m_per_px else None, source=source, estimated=estimated,
                check="; ".join(parts) if parts else "nothing independent to check against",
                disagreement_pct=round(abs(worst), 2) if worst is not None else None,
                denominator=(main_note or {}).get("denominator"), paper=(main_note or {}).get("paper") or sheet_paper,
                bare_unit=bare_unit, main=dict(note=(main_note or {}).get("text"), why=why_note,
                                               bar=" ".join(main_bar["labels"]) if main_bar else None),
                candidates=cands)


if __name__ == "__main__":  # python -m drawqa.scalebar texts.json (image.png | width_px height_px)
    import json, sys
    T = json.load(open(sys.argv[1]))
    T = T["texts"] if isinstance(T, dict) else T
    im = None
    if len(sys.argv) == 3:
        import cv2
        im = cv2.imread(sys.argv[2], cv2.IMREAD_GRAYSCALE)
        Hh, Ww = im.shape[:2]
    else:
        Ww, Hh = float(sys.argv[2]), float(sys.argv[3])
    r = scale(T, Ww, Hh, img=im)
    print(json.dumps({k: v for k, v in r.items() if k != "candidates"}, indent=1))
    for c in r["candidates"]:
        print("  ", c)
