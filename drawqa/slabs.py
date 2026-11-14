"""Slab area by type (thickness and bars) on a foundation / slab plan.

    callouts(texts)                               -> [slab callout: thickness, bars, spacing, faces, top of slab, text, bbox]
    dimension_chains(texts)                       -> scale (px per mm) and the dimension chains read from the numbers alone
    areas_by_type(texts, img=None, m_per_px=None) -> per slab type: area in m2, its region(s), how each number was derived
    answer(texts, img=None, m_per_px=None)        -> dict(answer, unit="m2", explanation, evidence, by_type)

`texts` is a sheet's text list ({text, bbox:[x0, y0, x1, y1]} in sheet pixels): OCR for images (textlayer.read), the
PDF text layer for vector sheets (pdfsheet.load(path)["texts"]). `img` is the sheet image (path or array, same pixels).

Where each number comes from:
  thickness, bars, spacing, faces, top of slab   read: the callout's own words ("300 SLAB-ON-GRADE W/ D16 @ 300 TOP FACE")
  scale (mm per pixel)                           read: a dimension number sits at the middle of its segment, so along a
                                                 chain  centre_i = start + k * (segments before it + half its own); k comes
                                                 out of the numbers alone and is checked against the printed 1:N
  which part of the plan a callout covers        estimated: the enclosed area the callout sits in (or its leader points
                                                 to), grown across single lines (control joints, dimension lines) but not
                                                 across walls or joints drawn as bands
  its area                                       measured: that outline's pixels at the chain scale; when dimension chains
                                                 inside it span it wall to wall and it is that rectangle, W x H of the
                                                 printed numbers (read)
"""
import math
import re

import numpy as np

# ---------------------------------------------------------------------------------------------------------------------
# text: lines and blocks
# ---------------------------------------------------------------------------------------------------------------------


def _bb(t):
    return [float(v) for v in t["bbox"]]


def _vertical(t):
    x0, y0, x1, y1 = t["bbox"]
    return len(t["text"].strip()) >= 2 and (y1 - y0) > 1.3 * (x1 - x0)


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)

    def groups(self, n):
        g = {}
        for i in range(n):
            g.setdefault(self.find(i), []).append(i)
        return list(g.values())


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / small if small > 0 else 0


def text_lines(texts):
    """Horizontal text items joined into lines (OCR splits '@ 300' into '@' and '300')."""
    items = [dict(text=t["text"].strip(), bbox=_bb(t)) for t in texts if t.get("text", "").strip() and not _vertical(t)]
    items.sort(key=lambda t: (t["bbox"][1] + t["bbox"][3]) / 2)
    n, uf = len(items), _UF(len(items))
    for i in range(n):
        a = items[i]["bbox"]
        ha, cya = a[3] - a[1], (a[1] + a[3]) / 2
        for j in range(i + 1, n):
            b = items[j]["bbox"]
            hb = b[3] - b[1]
            if (b[1] + b[3]) / 2 - cya > max(ha, hb):
                break
            if max(ha, hb) > 1.5 * min(ha, hb):
                continue
            if min(a[3], b[3]) - max(a[1], b[1]) < 0.5 * min(ha, hb):
                continue
            if max(b[0] - a[2], a[0] - b[2]) > 1.0 * max(ha, hb):
                continue
            uf.union(i, j)
    out = []
    for g in uf.groups(n):
        its = sorted((items[i] for i in g), key=lambda t: t["bbox"][0])
        kept = []
        for t in its:  # the same word read twice (overlapping OCR tiles): keep it once
            if not any(_iou(t["bbox"], k["bbox"]) > 0.6 for k in kept):
                kept.append(t)
        bb = [min(t["bbox"][0] for t in kept), min(t["bbox"][1] for t in kept),
              max(t["bbox"][2] for t in kept), max(t["bbox"][3] for t in kept)]
        hs = sorted(t["bbox"][3] - t["bbox"][1] for t in kept)
        out.append(dict(text=" ".join(t["text"] for t in kept), bbox=bb, h=hs[len(hs) // 2]))
    out.sort(key=lambda l: (l["bbox"][1], l["bbox"][0]))
    return out


def text_blocks(texts):
    """Lines stacked into blocks (a note or callout written over several lines)."""
    L = text_lines(texts)
    n, uf = len(L), _UF(len(L))
    for i in range(n):
        a = L[i]["bbox"]
        for j in range(i + 1, n):
            b = L[j]["bbox"]
            h = max(L[i]["h"], L[j]["h"])
            if b[1] - a[3] > 3 * h:
                break
            if h > 1.45 * min(L[i]["h"], L[j]["h"]):
                continue
            gap = b[1] - a[3]
            if gap > 0.75 * h or gap < -0.5 * h:
                continue
            ov = min(a[2], b[2]) - max(a[0], b[0])
            if abs(a[0] - b[0]) <= 1.5 * h or ov >= 0.5 * min(a[2] - a[0], b[2] - b[0]):
                uf.union(i, j)
    blocks = []
    for g in uf.groups(n):
        lines = sorted((L[i] for i in g), key=lambda l: l["bbox"][1])
        blocks.append(lines)
    return blocks


def _join(lines):
    s = ""
    for l in lines:
        t = l["text"]
        if s.endswith("-") and t[:1].isalnum():
            s += t  # "SLAB-ON-" + "GRADE"
        else:
            s += (" " if s else "") + t
    return s


# ---------------------------------------------------------------------------------------------------------------------
# callout grammar
# ---------------------------------------------------------------------------------------------------------------------

_U = r"(?:\"|''|IN(?:CH(?:ES)?)?\.?|MM|CM)"
_NUM = r"\d+(?:\.\d+)?(?:\s+\d/\d{1,2})?|\d/\d{1,2}"
SLAB_NOUN = (r"(?:SLABS?(?:[\s-]+ON[\s-]+(?:GRADE|GROUND|FILL|(?:METAL\s+)?DECK))?|\bS\.?O\.?G\b\.?|\bDECKE\b|BODENPLATTE|"
             r"\bLAATTA|\bPOHJALAATTA|\bDALLE\b|\bRADIER\b|\bTOPPING\b|\bMAT\s+FOUNDATION\b|\bRAFT\b)")
TOS = re.compile(r"(?:TOP\s+OF\s+(?:CONC(?:RETE)?\.?\s+)?SLAB|T\.?\s?O\.?\s?(?:S\b\.?|SLAB)|T/\s?SLAB|T\.\s?O\.\s?C\.?)\s*"
                 r"(?:EL(?:EV(?:ATION)?)?\.?)?\s*[=:]?\s*(?P<v>[+-]?\s?\d[\d,]*(?:\.\d+)?(?:\s*'\s*-?\s*\d+(?:\s+\d/\d{1,2})?\s*\")?)?")
NOT_A_SLAB = re.compile(r"\b(?:BELOW|UNDER|ABOVE|THROUGH|THRU|IN|INTO|SEE|BENEATH|ON|OF|FROM|TO)\s+(?:THE\s+)?(?:CONC(?:RETE)?\.?\s+)?SLABS?\b")
TH_BEFORE = re.compile(r"(?<![#@/\w.])(?P<t>" + _NUM + r")\s*(?P<u>" + _U + r")?\s*(?:THK\.?|THICK(?:NESS)?)?\s*"
                       r"(?:(?:R\.?C\.?|REINF(?:ORCED)?\.?|CONC(?:RETE)?\.?|STRUCTURAL|STRUCT\.?|NORMAL\s?WEIGHT|N\.?W\.?|L\.?W\.?|"
                       r"CAST[\s-]IN[\s-]PLACE|C\.?I\.?P\.?|FLAT|SOLID)\s*)*(?=" + SLAB_NOUN + ")")
TH_AFTER = re.compile(SLAB_NOUN + r"[^@#;]{0,30}?(?<![\d#@\w])(?P<t>" + _NUM + r")\s*(?P<u>" + _U + r")?\s*(?:THK\b\.?|THICK\b)")
TH_SYM = re.compile(r"(?<![A-Z])[DTH]\s*=\s*(?P<t>\d+(?:\.\d+)?)\s*(?P<u>MM|CM|M\b|\")?")
BAR = re.compile(r"(?<![A-Z0-9/])(?P<bar>#\s?\d{1,2}|[DTHYRNKØΦ⌀]\s?\d{1,2}|\d{2}M)(?!\d)\s*(?:BARS?\s*)?"
                 r"(?:@|\bAT\b|C/C|\bCC\b|\bK\s?(?=\d)|/(?=\s?\d))\s*(?P<sp>\d+(?:\.\d+)?(?:\s+\d/\d{1,2})?)\s*(?P<spu>" + _U + r")?"
                 r"\s*(?:O\.?\s?C\.?|C/C|CTRS?\.?|CENT(?:ER|RE)S?)?")
AS_INDICATED = re.compile(r"(?P<face>TOP|BOT(?:TOM|T)?\.?)\s*(?:FACE\s*)?(?:REINF(?:ORCING|ORCEMENT|\.)?|BARS?|STEEL)\s*"
                          r"(?P<how>AS\s+(?:INDICATED|SHOWN|NOTED|DETAILED)|PER\s+[\w/-]+|SEE\s+[\w/-]+)")
FACES = [("top & bottom", r"T\s?&\s?B\b|TOP\s*(?:&|AND)\s*BOT(?:TOM|T)?\b\.?"),
         ("top", r"TOP(?!\s+OF)(?:\s+FACE)?|\bT\.\s?F\.|\bT[12]\b"),
         ("bottom", r"\bBOT(?:TOM|T)?\b\.?(?:\s+FACE)?|\bB\.\s?F\.|\bB[12]\b"),
         ("each way", r"\bE\.\s?W\.|\bEW\b|EA\.?\s+WAY|EACH\s+WAY|\bB\.\s?W\.|\bBW\b|BOTH\s+WAYS"),
         ("each face", r"\bE\.\s?F\.|\bEF\b|EACH\s+FACE")]
MESH = re.compile(r"\b\d+\s?[X×]\s?\d+\s*-?\s*(?:W|D|MW)\d+(?:\.\d+)?\s*[X×]\s*(?:W|D|MW)\d+(?:\.\d+)?(?:\s*(?:WWF|WWR|WWM|W\.W\.[FMR]\.?))?"
                  r"|\b\d+\s?[X×]\s?\d+\s*(?:WWF|WWR|WWM)\b|\b(?:WWF|WWR|WWM)\b|\b[ABR]\d{3}\b(?=.*MESH)|\bSL\s?\d{2,3}\b")
SCALE = re.compile(r"(?:SCALE|MITTAKAAVA|MASSSTAB|MAßSTAB|ECHELLE|SCALA)?\s*[:=]?\s*1\s*[:/]\s*(\d{1,4})\b", re.I)


def _num(s):
    s = s.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)?\s*(?:(\d+)/(\d+))?", s)
    if not m:
        return None
    v = float(m.group(1)) if m.group(1) else 0.0
    if m.group(2):
        v += int(m.group(2)) / int(m.group(3))
    return v


def _to_mm(v, unit, inch_hint, lo=50):
    """A length with its unit, or with the unit decided by its size (and said so)."""
    u = (unit or "").upper().strip(". ")
    if u in ('"', "''", "IN", "INCH", "INCHES"):
        return v * 25.4, "in", False
    if u == "CM":
        return v * 10, "cm", False
    if u == "MM":
        return v, "mm", False
    if u == "M":
        return v * 1000, "m", False
    if inch_hint and v <= 24:
        return v * 25.4, "in", True
    if v >= lo:
        return v, "mm", True
    if v <= 24:
        return v * 25.4, "in", True
    return v * 10, "cm", True


def _bar_mm(bar):
    b = bar.replace(" ", "")
    if b.startswith("#"):
        return round(int(b[1:]) * 25.4 / 8, 1)
    if b.endswith("M"):
        return {10: 11.3, 15: 16.0, 20: 19.5, 25: 25.2, 30: 29.9, 35: 35.7}.get(int(b[:-1]), float(b[:-1]))
    return float(b[1:])


def parse_callout(text):
    """One slab callout's words -> its parts, or None when the text does not name a slab with a thickness or bars."""
    U = " ".join(text.upper().split())
    tos = TOS.search(U)
    rest = TOS.sub(" ", U)
    rest = NOT_A_SLAB.sub(" ", rest)
    if not re.search(SLAB_NOUN, rest):
        return None
    notes = []
    for m in AS_INDICATED.finditer(rest):
        face = "top" if m.group("face").startswith("TOP") else "bottom"
        notes.append(f"{face} face reinforcing {m.group('how').lower()}")
    scan = AS_INDICATED.sub(" ", rest)
    bars_m = list(BAR.finditer(scan))
    inch_hint = any(m.group("bar").startswith("#") for m in bars_m) or '"' in U or "O.C" in U
    th = None
    for rx in (TH_BEFORE, TH_AFTER):
        m = rx.search(rest)
        if m and _num(m.group("t")) is not None:
            th = (m, _num(m.group("t")))
            break
    if th is None:
        m = TH_SYM.search(rest)
        if m:
            th = (m, float(m.group("t")))
    out = dict(text=" ".join(text.split()), thickness_mm=None, thickness_text=None, thickness_unit=None, unit_assumed=False,
               kind="slab-on-grade" if re.search(r"ON[\s-]+(?:GRADE|GROUND|FILL)|\bS\.?O\.?G\b", U) else "slab",
               bars=[], mesh=None, notes=notes, top_elevation=None, top_elevation_value=None)
    if th is not None:
        m, v = th
        mm, unit, assumed = _to_mm(v, m.group("u"), inch_hint)
        if 50 <= mm <= 3000:  # a slab is 2" .. 3 m thick; anything else is some other number
            out.update(thickness_mm=round(mm, 1), thickness_text=m.group(0).strip(), thickness_unit=unit, unit_assumed=assumed)
    for i, m in enumerate(bars_m):
        sp = _num(m.group("sp"))
        if sp is None:
            continue
        is_us = m.group("bar").startswith("#")
        spmm, spu, sp_assumed = _to_mm(sp, m.group("spu"), is_us or inch_hint)
        end = bars_m[i + 1].start() if i + 1 < len(bars_m) else len(scan)
        seg = scan[m.end():min(end, m.end() + 40)]
        cut = re.search(r"\.\s+(?!WAY|FACE|EA\b|E\.|EACH|B\.|T\.|T\s?&|BOT|TOP(?!\s+OF)|&)", seg)
        if cut:
            seg = seg[:cut.start() + 1]
        seg = TOS.sub(" ", seg)
        faces = []
        for name, rx in FACES:
            if re.search(rx, seg) and not (name in ("top", "bottom") and "top & bottom" in faces):
                faces.append(name)
        out["bars"].append(dict(bar=m.group("bar").replace(" ", ""), diameter_mm=_bar_mm(m.group("bar")),
                                spacing_mm=round(spmm, 1), spacing_text=m.group(0).strip(), spacing_unit=spu,
                                unit_assumed=sp_assumed, face=", ".join(faces) or None))
    mm = MESH.search(scan)
    if mm:
        out["mesh"] = mm.group(0).strip()
    if tos:
        out["top_elevation"] = tos.group(0).strip()
        if tos.group("v"):
            v = tos.group("v").replace(" ", "")
            out["top_elevation_value"] = float(v.replace(",", "")) if re.fullmatch(r"[+-]?\d[\d,]*(?:\.\d+)?", v) else v
    if out["thickness_mm"] is None and not (out["bars"] or out["mesh"]):
        return None  # "CONCRETE SLAB", "SLAB ON GRADE, SEE PLAN": a slab is named, but no type is given
    if out["thickness_mm"] is None and out["kind"] != "slab-on-grade" and not re.search(r"\bSLAB\b", rest):
        return None
    return out


def _fmt_mm(v):
    return f"{v:g}"


def _m(mm):
    return f"{mm / 1000:.3f}".rstrip("0").rstrip(".") + " m"


def _chain_text(values, texts=None):
    """How a chain was printed: 5800+7400+6300, or 36'-0"+24'-0" for feet and inches."""
    if texts and any(not float(v).is_integer() or "'" in str(t) for v, t in zip(values, texts)):
        parts = [str(t).strip() for t in texts]
    else:
        parts = [f"{v:g}" for v in values]
    return parts[0] if len(parts) == 1 else "+".join(parts) if len(parts) <= 3 else f"{len(parts)} segments"


def type_key(c):
    bars = tuple(sorted((b["bar"], round(b["spacing_mm"]), b["face"] or "") for b in c["bars"]))
    return (round(c["thickness_mm"]) if c["thickness_mm"] else None, bars, c["mesh"])


def type_label(c):
    if c["thickness_mm"] is None:
        th = "thickness not given"
    elif c["thickness_unit"] == "in":
        th = f"{c['thickness_mm'] / 25.4:g}\" ({c['thickness_mm']:.0f} mm)"
    else:
        th = f"{_fmt_mm(c['thickness_mm'])} mm"
    parts = [f"{th} {c['kind']}"]
    for b in c["bars"]:
        sp = f"{b['spacing_mm'] / 25.4:g}\"" if b["spacing_unit"] == "in" else f"{_fmt_mm(b['spacing_mm'])}"
        parts.append(f"{b['bar']} @ {sp}" + (f" {b['face']}" if b["face"] else ""))
    if c["mesh"]:
        parts.append(c["mesh"])
    s = parts[0] + (", " + "; ".join(parts[1:]) if len(parts) > 1 else "")
    if c["notes"]:
        s += " (" + "; ".join(c["notes"]) + ")"
    return s


def callouts(texts):
    """Every slab callout on the sheet: its words joined across lines, parsed, with its box and line boxes."""
    out = []
    for lines in text_blocks(texts):
        starts = [i for i, l in enumerate(lines) if TH_BEFORE.search(" ".join(l["text"].upper().split())) or
                  (i + 1 < len(lines) and TH_BEFORE.search(" ".join(_join(lines[i:i + 2]).upper().split()))
                   and not TH_BEFORE.search(" ".join(lines[i + 1]["text"].upper().split())))]
        cuts = sorted(set([0] + [s for s in starts if s > 0]))
        for a, b in zip(cuts, cuts[1:] + [len(lines)]):
            part = lines[a:b]
            c = parse_callout(_join(part))
            if c is None:
                continue
            bb = [min(l["bbox"][0] for l in part), min(l["bbox"][1] for l in part),
                  max(l["bbox"][2] for l in part), max(l["bbox"][3] for l in part)]
            c.update(bbox=[round(v, 1) for v in bb], line_bboxes=[[round(v, 1) for v in l["bbox"]] for l in part],
                     line_height=sorted(l["h"] for l in part)[len(part) // 2], label=type_label(c))
            c["id"] = f"slab{len(out)}"
            out.append(c)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# dimension chains and scale
# ---------------------------------------------------------------------------------------------------------------------

FT_IN = re.compile(r"(\d{1,3})\s*['’]\s*-?\s*(?:(\d{1,2}(?:\s+\d/\d{1,2})?|\d/\d{1,2})\s*(?:\"|”|''))?")
_FLIP = str.maketrans("69", "96")


def dimension_numbers(texts):
    """Bare numbers (dimension texts). A vertical number read upside down ('009') is turned back ('600')."""
    out = []
    for t in texts:
        s = t["text"].strip().lstrip("-~").replace(",", "")
        x0, y0, x1, y1 = _bb(t)
        vert = _vertical(t)
        m = FT_IN.fullmatch(s)
        if m and not re.fullmatch(r"\d+", s):  # 12'-6" -> 3810 mm
            v = int(m.group(1)) * 304.8 + (_num(m.group(2)) or 0) * 25.4 if m.group(2) else int(m.group(1)) * 304.8
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            out.append(dict(value=round(v, 1), text=t["text"], bbox=[x0, y0, x1, y1], axis="y" if vert else "x", flipped=False,
                            along=cy if vert else cx, cross=cx if vert else cy, size=(x1 - x0) if vert else (y1 - y0)))
            continue
        if not re.fullmatch(r"\d{2,6}", s):
            continue
        flipped = False
        if s.startswith("0"):
            if vert and set(s) <= set("01689"):
                s, flipped = s[::-1].translate(_FLIP), True
            if s.startswith("0"):
                continue
        v = int(s)
        if v < 10:
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        out.append(dict(value=v, text=t["text"], bbox=[x0, y0, x1, y1], axis="y" if vert else "x", flipped=flipped,
                        along=cy if vert else cx, cross=cx if vert else cy, size=(x1 - x0) if vert else (y1 - y0)))
    return out


def _rows(nums):
    rows = []
    for axis in ("x", "y"):
        ns = sorted((n for n in nums if n["axis"] == axis), key=lambda n: n["cross"])
        cur = []
        for n in ns:
            if cur and (n["cross"] - cur[-1]["cross"] > 0.5 * max(n["size"], cur[-1]["size"])):
                rows.append(cur)
                cur = []
            cur.append(n)
        if cur:
            rows.append(cur)
    return [sorted(r, key=lambda n: n["along"]) for r in rows]


def _scale_from_pairs(rows):
    ks = []
    for r in rows:
        for a, b in zip(r, r[1:]):
            if max(a["size"], b["size"]) > 1.4 * min(a["size"], b["size"]):
                continue
            d = b["along"] - a["along"]
            if d > 0:
                ks.append(d / ((a["value"] + b["value"]) / 2))
    if len(ks) < 3:
        return None, 0
    lk = np.log(np.array(ks))
    votes = [(int(np.sum(np.abs(lk - x) < 0.02)), x) for x in lk]
    n, centre = max(votes)
    if n < 3:
        return None, n
    return float(np.exp(np.median(lk[np.abs(lk - centre) < 0.02]))), n


def dimension_chains(texts, px_per_mm=None):
    """Read the dimension chains: numbers on one line whose spacing matches one scale.

    Returns dict(px_per_mm, pairs (how many neighbouring numbers agree on it), chains=[dict(axis, values, total, start,
    end, cross, ids)], closures=[(chain, overall)] where a chain adds up to an overall dimension printed beside it)."""
    nums = dimension_numbers(texts)
    rows = _rows(nums)
    k, votes = (px_per_mm, None) if px_per_mm else _scale_from_pairs(rows)
    chains = []
    if not k:
        return dict(px_per_mm=None, pairs=votes, chains=[], closures=[], numbers=nums)
    for r in rows:
        left = list(r)
        while left:
            ch = [left.pop(0)]
            i = 0
            while i < len(left):
                last, cand = ch[-1], left[i]
                exp = k * (last["value"] + cand["value"]) / 2
                act = cand["along"] - last["along"]
                tol = max(0.03 * exp, 0.6 * max(last["size"], cand["size"]))
                if abs(act - exp) <= tol and max(last["size"], cand["size"]) <= 1.4 * min(last["size"], cand["size"]):
                    ch.append(left.pop(i))
                    continue
                if act > k * (last["value"] / 2 + max(n["value"] for n in left) / 2) * 1.1 + tol:
                    break
                i += 1
            cum, starts = 0, []
            for n in ch:
                starts.append(n["along"] - k * (cum + n["value"] / 2))
                cum += n["value"]
            s0 = float(np.mean(starts))
            chains.append(dict(axis=ch[0]["axis"], values=[n["value"] for n in ch], total=round(cum, 1), texts=[n["text"] for n in ch], start=s0, end=s0 + k * cum,
                               cross=float(np.mean([n["cross"] for n in ch])), size=float(np.median([n["size"] for n in ch])),
                               items=ch, flipped=sum(n["flipped"] for n in ch)))
    closures = []
    for c in chains:
        if len(c["values"]) < 2:
            continue
        for d in chains:
            if d is c or d["axis"] != c["axis"] or abs(d["total"] - c["total"]) > max(1.0, 0.001 * c["total"]):
                continue
            tol = max(0.01 * k * c["total"], 1.0 * c["size"])
            if abs(d["start"] - c["start"]) <= tol and abs(d["end"] - c["end"]) <= tol and (len(d["values"]) < len(c["values"])):
                closures.append((c, d))
    return dict(px_per_mm=k, pairs=votes, chains=chains, closures=closures, numbers=nums)


IMPERIAL_SCALE = re.compile(r"""(?:(\d+)\s+)?(\d+)\s*/\s*(\d+)\s*(?:"|IN\.?)\s*=\s*1\s*'|(\d+)\s*(?:"|IN\.?)\s*=\s*(\d+)\s*'""", re.I)


def _printed_scale(texts):
    """Printed scale denominators: 1:50 -> 50, 1/4" = 1'-0" -> 48."""
    dens = set()
    for t in texts:
        s = t["text"]
        m = IMPERIAL_SCALE.search(s)
        if m:
            if m.group(2):
                inch = int(m.group(1) or 0) + int(m.group(2)) / int(m.group(3))
                if inch:
                    dens.add(round(12 / inch, 2))
            elif int(m.group(4)):
                dens.add(round(int(m.group(5)) * 12 / int(m.group(4)), 2))
            continue
        if re.search(r"SCALE|MITTAKAAVA|MA(?:SS|ß)STAB|[ÉE]CHELLE|SCALA|^\s*1\s*:\s*\d+\s*$", s, re.I):
            m = SCALE.search(s)
            if m:
                dens.add(int(m.group(1)))
    return sorted(dens)


# ---------------------------------------------------------------------------------------------------------------------
# regions from the linework
# ---------------------------------------------------------------------------------------------------------------------


def _gray(img):
    import cv2
    if isinstance(img, str):
        g = cv2.imread(img, cv2.IMREAD_GRAYSCALE)
        if g is None:
            raise FileNotFoundError(img)
        return g
    a = np.asarray(img)
    if a.ndim == 3:
        return cv2.cvtColor(a[:, :, :3], cv2.COLOR_BGR2GRAY)
    return a


def _rect_dist(p, r):
    dx = max(r[0] - p[0], 0, p[0] - r[2])
    dy = max(r[1] - p[1], 0, p[1] - r[3])
    return math.hypot(dx, dy)


class _Plan:
    """The sheet's white space split into enclosed areas (4-connected, so a hairline closes an area)."""

    def __init__(self, img, chains=(), envelope=None, max_side=5000):
        import cv2
        g = _gray(img)
        self.H0, self.W0 = g.shape[:2]
        self.f = max(1, math.ceil(max(self.H0, self.W0) / max_side))
        f = self.f
        if f > 1:  # darkest pixel of each f x f block: a 1 px line stays a line
            small = g[:self.H0 // f * f, :self.W0 // f * f].reshape(self.H0 // f, f, self.W0 // f, f).min(axis=(1, 3))
        else:
            small = g
        del g
        self.dark = (small < 160).astype(np.uint8)
        del small
        self.H, self.W = self.dark.shape
        self.erased = self._erase_dimension_lines(chains)
        self.n, self.lab, self.stats, _ = cv2.connectedComponentsWithStats(1 - self.dark, connectivity=4)
        self.env = [v / self.f for v in envelope] if envelope else None
        self._radius = {}

    def _erase_dimension_lines(self, chains):
        """A dimension line is annotation, not an edge: the thin line beside each read chain is removed, so it neither
        cuts a slab in two nor closes a pocket of paper between the dimensions and the building."""
        f, n = self.f, 0
        for c in chains:
            a, b = c["start"] / f, c["end"] / f
            L = b - a
            if L < 20:
                continue
            lo, hi = int(a + 0.1 * L), int(b - 0.1 * L)
            cross, reach = c["cross"] / f, int(1.6 * c["size"] / f) + 2
            best = None
            for d in range(-reach, reach + 1):
                p = int(round(cross + d))
                if c["axis"] == "x":
                    if not 0 <= p < self.H:
                        continue
                    cov = self.dark[p, lo:hi].mean() if hi > lo else 0
                else:
                    if not 0 <= p < self.W:
                        continue
                    cov = self.dark[lo:hi, p].mean() if hi > lo else 0
                if best is None or cov > best[0]:
                    best = (cov, p)
            if best is None or best[0] < 0.6:
                continue
            p = best[1]
            band = [q for q in range(p - 3, p + 4) if 0 <= q < (self.H if c["axis"] == "x" else self.W) and
                    (self.dark[q, lo:hi].mean() if c["axis"] == "x" else self.dark[lo:hi, q].mean()) >= 0.6]
            if len(band) > 3:  # a thick line next to the numbers is a wall or a joint: leave it
                continue
            s, e = max(0, int(a) - 2), int(b) + 3
            q0, q1 = min(band) - 3, max(band) + 3
            if c["axis"] == "x":
                if q0 < 0 or q1 >= self.H:
                    continue
                keep = (self.dark[q0, s:e] > 0) & (self.dark[q1, s:e] > 0)  # a line crossing it (a wall): keep
                for q in band:
                    self.dark[q, s:e][~keep] = 0
            else:
                if q0 < 0 or q1 >= self.W:
                    continue
                keep = (self.dark[s:e, q0] > 0) & (self.dark[s:e, q1] > 0)
                for q in band:
                    self.dark[s:e, q][~keep] = 0
            n += 1
        return n

    def exterior(self, L):
        x, y, w, h, _ = self.stats[L]
        if x == 0 or y == 0 or x + w >= self.W or y + h >= self.H or w * h > 0.4 * self.W * self.H:
            return True
        if self.env:  # beyond the overall dimensions: paper outside the building
            e, t = self.env, 0.01 * max(self.env[2] - self.env[0], self.env[3] - self.env[1])
            return x < e[0] - t or y < e[1] - t or x + w > e[2] + t or y + h > e[3] + t
        return False

    def exterior_lut(self):
        x, y, w, h = (self.stats[:, i] for i in range(4))
        ext = (x == 0) | (y == 0) | (x + w >= self.W) | (y + h >= self.H) | (w.astype(np.int64) * h > 0.4 * self.W * self.H)
        if self.env:
            e, t = self.env, 0.01 * max(self.env[2] - self.env[0], self.env[3] - self.env[1])
            ext |= (x < e[0] - t) | (y < e[1] - t) | (x + w > e[2] + t) | (y + h > e[3] + t)
        ext[0] = False
        return ext

    def footprint(self, seeds_xy, k_small):
        """The building's outline: everything inside the overall dimensions that is not open paper around it, with thin
        lines reaching out of it (extension lines, leaders) cut off; the blob that holds the slab areas, holes filled."""
        import cv2
        if not self.env:
            return None
        e = [max(0, int(self.env[0]) - 2), max(0, int(self.env[1]) - 2), min(self.W, int(self.env[2]) + 3), min(self.H, int(self.env[3]) + 3)]
        m = (~self.exterior_lut()[self.lab[e[1]:e[3], e[0]:e[2]]]).astype(np.uint8)
        q = max(3, int(round(150 * k_small)))  # thinner than 0.15 m: a line, not building
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (q, q)))
        n, lab = cv2.connectedComponents(m, connectivity=8)
        keep = {int(lab[int(y) - e[1], int(x) - e[0]]) for x, y in seeds_xy
                if 0 <= int(y) - e[1] < lab.shape[0] and 0 <= int(x) - e[0] < lab.shape[1]} - {0}
        if not keep:
            return None
        m = np.isin(lab, list(keep)).astype(np.uint8)
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(m)
        cv2.drawContours(filled, cs, -1, 1, -1)
        polys = [[[float((x + e[0]) * self.f), float((y + e[1]) * self.f)] for x, y in cv2.approxPolyDP(c, 1.5, True)[:, 0, :]]
                 for c in sorted(cs, key=cv2.contourArea, reverse=True)[:2]]
        return int(filled.sum()), polys

    def radius(self, L):
        """Largest circle inside area L (px): tells a slab panel from the strip between a wall's two lines."""
        import cv2
        if L not in self._radius:
            x, y, w, h, _ = self.stats[L]
            m = (self.lab[y:y + h, x:x + w] == L).astype(np.uint8)
            m = np.pad(m, 1)
            self._radius[L] = float(cv2.distanceTransform(m, cv2.DIST_L2, 3).max())
        return self._radius[L]

    def label_at(self, x, y, rad):
        x, y = int(round(x)), int(round(y))
        x0, y0, x1, y1 = max(0, x - rad), max(0, y - rad), min(self.W, x + rad + 1), min(self.H, y + rad + 1)
        if x0 >= x1 or y0 >= y1:
            return None
        win = self.lab[y0:y1, x0:x1]
        ys, xs = np.nonzero(win)
        if not len(xs):
            return None
        i = int(np.argmin((xs + x0 - x) ** 2 + (ys + y0 - y) ** 2))
        return int(win[ys[i], xs[i]])

    def neighbours(self, R, r):
        import cv2
        xs = [self.stats[L] for L in R]
        x0 = max(0, min(s[0] for s in xs) - r - 1)
        y0 = max(0, min(s[1] for s in xs) - r - 1)
        x1 = min(self.W, max(s[0] + s[2] for s in xs) + r + 1)
        y1 = min(self.H, max(s[1] + s[3] for s in xs) + r + 1)
        crop = self.lab[y0:y1, x0:x1]
        m = np.isin(crop, list(R)).astype(np.uint8)
        d = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        c = np.unique(crop[(d > 0) & (m == 0)])
        return [int(v) for v in c if v != 0]

    def outline(self, R, r):
        """Area R as one filled shape: areas joined across the single lines between them, holes (text, symbols) filled."""
        import cv2
        xs = [self.stats[L] for L in R]
        x0 = max(0, min(s[0] for s in xs) - r - 2)
        y0 = max(0, min(s[1] for s in xs) - r - 2)
        x1 = min(self.W, max(s[0] + s[2] for s in xs) + r + 2)
        y1 = min(self.H, max(s[1] + s[3] for s in xs) + r + 2)
        m = np.isin(self.lab[y0:y1, x0:x1], list(R)).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(m)
        cv2.drawContours(filled, cs, -1, 1, -1)
        return filled, (x0, y0), cs

    def leader_tip(self, c, h):
        """Follow a leader line from the callout text to its far end (small-image px), or None."""
        import cv2
        f = self.f
        bb = [v / f for v in c["bbox"]]
        hs = max(4.0, h / f)
        E = int(14 * hs + max(bb[2] - bb[0], bb[3] - bb[1]))
        X0, Y0 = max(0, int(bb[0]) - E), max(0, int(bb[1]) - E)
        X1, Y1 = min(self.W, int(bb[2]) + E), min(self.H, int(bb[3]) + E)
        crop = self.dark[Y0:Y1, X0:X1].copy()
        for lb in c["line_bboxes"]:  # the words themselves are not lines
            a = [int(v / f) for v in lb]
            crop[max(0, a[1] - Y0 - 1):max(0, a[3] - Y0 + 2), max(0, a[0] - X0 - 1):max(0, a[2] - X0 + 2)] = 0
        segs = cv2.HoughLinesP(crop * 255, 1, np.pi / 360, threshold=max(12, int(hs)), minLineLength=max(8, int(1.0 * hs)), maxLineGap=2)
        if segs is None:
            return None
        segs = [((s[0] + X0, s[1] + Y0), (s[2] + X0, s[3] + Y0)) for s in np.asarray(segs).reshape(-1, 4).tolist()]
        first, last = c["line_bboxes"][0], c["line_bboxes"][-1]
        best = None
        for i, (p, q) in enumerate(segs):
            dp, dq = _rect_dist(p, bb), _rect_dist(q, bb)
            near, far = (p, q) if dp <= dq else (q, p)
            if min(dp, dq) > 0.8 * hs or max(dp, dq) < 1.5 * hs:
                continue
            # a leader leaves from the end of the first or last line
            ys = [first[1] / f, first[3] / f, last[1] / f, last[3] / f]
            side = (abs(near[0] - bb[0]) < 1.2 * hs or abs(near[0] - bb[2]) < 1.2 * hs or near[1] < bb[1] + 0.5 * hs or near[1] > bb[3] - 0.5 * hs)
            at_line = any(ys[j] - 0.5 * hs <= near[1] <= ys[j + 1] + 0.5 * hs for j in (0, 2))
            # a horizontal shoulder may bend once into the leg; the leg is then traced pixel by pixel to its end
            ang = math.atan2(far[1] - near[1], far[0] - near[0])
            start, d = near, (far[0] - near[0], far[1] - near[1])
            if abs(math.sin(ang)) < math.sin(math.radians(10)):
                bend = None
                for j, (a, b) in enumerate(segs):
                    if j == i:
                        continue
                    for u, v in ((a, b), (b, a)):
                        if math.dist(u, far) <= 0.6 * hs and _rect_dist(v, bb) > _rect_dist(far, bb) + 0.5 * hs:
                            t = math.atan2(v[1] - u[1], v[0] - u[0])
                            if abs(math.sin(t - ang)) > math.sin(math.radians(15)) and (bend is None or math.dist(u, v) > bend[0]):
                                bend = (math.dist(u, v), u, v)
                if bend:
                    start, d = far, (bend[2][0] - bend[1][0], bend[2][1] - bend[1][1])
            tip, d2 = self._trace(start, d, gap=max(3, int(0.4 * hs)), limit=int(40 * hs))
            path = [near, far] + ([start] if start is far else []) + [tip]
            path = [p_ for k_, p_ in enumerate(path) if k_ == 0 or p_ != path[k_ - 1]]
            length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
            score = (side and at_line, length)
            if best is None or score > best[0]:
                best = (score, path, d2)
        if best is None:
            return None
        path = best[1]
        return dict(path=path, tip=path[-1], direction=best[2])

    def _trace(self, p, d, gap, limit):
        """Walk along a line from p in direction d while it stays dark (crossing lines do not stop it); its far end.
        A sideways step of one pixel keeps it on a slightly mis-aimed line; two close together would let a crossing
        line drag it away, so they are not allowed, and the direction is only re-aimed over the whole distance walked."""
        x0, y0 = float(p[0]), float(p[1])
        x, y = x0, y0
        n = math.hypot(*d) or 1.0
        dx, dy = d[0] / n, d[1] / n
        last, miss, since_side, walked = (x, y), 0, 99, 0
        for _ in range(limit):
            cx, cy = x + dx, y + dy
            hit = None
            for o in ((0, -1, 1) if since_side >= 3 else (0,)):
                qx, qy = cx - dy * o, cy + dx * o
                ix, iy = int(round(qx)), int(round(qy))
                if 0 <= ix < self.W and 0 <= iy < self.H and self.dark[iy, ix]:
                    hit = (qx, qy, o)
                    break
            since_side += 1
            if hit:
                x, y = hit[0], hit[1]
                if hit[2]:
                    since_side = 0
                last, miss = (x, y), 0
                walked += 1
                if walked % 8 == 0 and math.hypot(x - x0, y - y0) > 30:
                    m = math.hypot(x - x0, y - y0)
                    dx, dy = (x - x0) / m, (y - y0) / m
            else:
                x, y = cx, cy
                miss += 1
                if miss > gap:
                    break
        return (float(last[0]), float(last[1])), (dx, dy)


def _run(mask, x, y, axis):
    """Length of the run of mask pixels through (x, y) along the axis ('x' horizontal)."""
    H, W = mask.shape
    x, y = int(round(x)), int(round(y))
    if not (0 <= x < W and 0 <= y < H) or not mask[y, x]:
        return None
    line = mask[y, :] if axis == "x" else mask[:, x]
    i = x if axis == "x" else y
    a = i
    while a > 0 and line[a - 1]:
        a -= 1
    b = i
    while b < len(line) - 1 and line[b + 1]:
        b += 1
    return a, b + 1


def areas_by_type(texts, img=None, m_per_px=None):
    """Slab types on the sheet with their areas, the region each covers, and how each number was obtained."""
    cos = callouts(texts)
    hs = sorted(t["bbox"][3] - t["bbox"][1] for t in texts if t.get("text", "").strip() and not _vertical(t)) or [20]
    h_med = hs[len(hs) // 2]
    k_given = 1 / (m_per_px * 1000) if m_per_px else None
    dims = dimension_chains(texts)
    k_chain = dims["px_per_mm"]
    printed = _printed_scale(texts)
    scale = dict(px_per_mm=k_given or k_chain, source=None, chain_px_per_mm=k_chain, chain_pairs=dims["pairs"], printed=printed)
    warn = []
    if k_given and k_chain and abs(k_chain / k_given - 1) > 0.03 and (dims["pairs"] or 0) >= 5:
        # many printed dimensions agree with each other and not with the given scale: believe the dimensions
        warn.append(f"the given scale (1 px = {1 / k_given:.3g} mm) disagrees with {dims['pairs']} dimension numbers "
                    f"(1 px = {1 / k_chain:.3g} mm); the dimensions are used")
        k_given = None
        scale["px_per_mm"] = k_chain
    if k_given and printed:
        dpis = [k_given * d * 25.4 for d in printed]
        std = (72, 96, 100, 144, 150, 200, 240, 300, 400, 600)
        if not any(abs(min(std, key=lambda x: abs(x / v - 1)) / v - 1) < 0.03 for v in dpis):
            warn.append(f"the given scale (1 px = {1 / k_given:.3g} mm) fits the printed "
                        f"{', '.join('1:' + format(d, 'g') for d in printed)} at no standard resolution: check it")
    if k_given:
        scale["source"] = "given (drawing scale and render resolution)"
        if k_chain and abs(k_chain / k_given - 1) > 0.03:
            warn.append(f"dimension chains say 1 px = {1 / k_chain:.3g} mm, the given scale says {1 / k_given:.3g} mm")
        if not k_chain or abs(k_chain / k_given - 1) > 0.03:
            dims = dimension_chains(texts, px_per_mm=k_given)  # chains read at the given scale
    elif k_chain:
        scale["source"] = f"{dims['pairs']} neighbouring dimension numbers agree on one spacing (1 px = {1 / k_chain:.3g} mm)"
        if len(printed) == 1:
            dpi = k_chain * printed[0] * 25.4
            common = min((72, 96, 100, 144, 150, 200, 240, 300, 400, 600), key=lambda d: abs(d / dpi - 1))
            scale["printed_check"] = (f"printed 1:{printed[0]} with this scale means a {dpi:.0f} dpi image"
                                      + (f" (a standard {common} dpi: consistent)" if abs(common / dpi - 1) < 0.03 else " (not a standard resolution: check)"))
    if warn:
        scale["warning"] = "; ".join(warn)
    k = scale["px_per_mm"]
    closures = {}  # chains that add up to the same overall dimension: one check each
    for c, d in dims["closures"]:
        g = closures.setdefault((c["axis"], round(c["total"])), dict(axis=c["axis"], total=c["total"], chains=[], shown=[]))
        for ch in sorted((c, d), key=lambda ch: len(ch["values"])):
            if ch["values"] not in g["chains"]:
                g["chains"].append(ch["values"])
                g["shown"].append(_chain_text(ch["values"], ch.get("texts")))
    closures = list(closures.values())
    res = dict(callouts=cos, scale=scale, closures=closures, types=[], checks=[], h_text=h_med)
    if not cos:
        return res
    by_key = {}
    for c in cos:
        by_key.setdefault(type_key(c), []).append(c)
    if img is None:
        for key, cs in by_key.items():
            res["types"].append(dict(key=key, label=cs[0]["label"], callouts=[c["id"] for c in cs], area_m2=None, regions=[],
                                     tier="cannot determine", unassigned_callouts=[c["id"] for c in cs],
                                     how="no drawing image given: the outline each callout covers cannot be traced from text alone"))
        return res

    envelope = None  # the overall dimensions bound the building: [x0, y0, x1, y1] in sheet px
    if dims["chains"]:
        ext = {}
        for axis in ("x", "y"):
            closed = [c for pair in dims["closures"] for c in pair if c["axis"] == axis]
            chs = closed or [c for c in dims["chains"] if c["axis"] == axis and len(c["values"]) >= 2]
            if chs:
                top = max(c["total"] for c in chs)
                big = [c for c in chs if c["total"] >= 0.6 * top]
                ext[axis] = (min(c["start"] for c in big), max(c["end"] for c in big))
        if len(ext) == 2:
            envelope = [ext["x"][0], ext["y"][0], ext["x"][1], ext["y"][1]]
    res["envelope_px"] = envelope
    plan = _Plan(img, chains=dims["chains"], envelope=envelope)
    res["dimension_lines_erased"] = plan.erased
    f = plan.f
    ks = k / f if k else None
    r = max(2, int(round(0.3 * h_med / f)))  # a line this thin is a joint or a dimension line, not a wall
    min_r = 250 * ks if ks else 0.6 * h_med / f  # an area narrower than 0.5 m is the inside of a wall, not a slab
    hs_ = h_med / f

    def frame_of(L, c):  # the box drawn around the callout's own text
        x, y, w, h, _ = plan.stats[L]
        bb = [v / f for v in c["bbox"]]
        p = 1.5 * c["line_height"] / f
        return x >= bb[0] - p and y >= bb[1] - p and x + w <= bb[2] + p and y + h <= bb[3] + p

    def ok(L, c=None):
        return not plan.exterior(L) and plan.radius(L) >= min_r and not (c is not None and frame_of(L, c))

    seeds, how_seed, leaders = {}, {}, {}
    for c in cos:
        bb = [v / f for v in c["bbox"]]
        p = 0.9 * c["line_height"] / f
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        pts = [(cx, cy), (bb[0] - p, cy), (bb[2] + p, cy), (cx, bb[1] - p), (cx, bb[3] + p),
               (bb[0] - p, bb[1] - p), (bb[2] + p, bb[1] - p), (bb[0] - p, bb[3] + p), (bb[2] + p, bb[3] + p)]
        votes = {}
        for x, y in pts:
            L = plan.label_at(x, y, max(2, int(0.3 * hs_)))
            if L and ok(L, c):
                votes[L] = votes.get(L, 0) + 1
        if votes:
            L = max(votes, key=lambda L: (votes[L], plan.stats[L][4]))
            if votes[L] >= 3:
                seeds[c["id"]], how_seed[c["id"]] = L, "the callout sits inside it"
                continue
        tip = plan.leader_tip(c, c["line_height"])
        if tip:
            leaders[c["id"]] = tip
            (tx, ty), (dx, dy) = tip["tip"], tip["direction"]
            n = math.hypot(dx, dy) or 1
            for u in (0.4, 0.8, 1.2, 0.0, 2.0):
                L = plan.label_at(tx + dx / n * u * hs_, ty + dy / n * u * hs_, max(1, int(0.2 * hs_)))
                if L and ok(L, c):
                    seeds[c["id"]], how_seed[c["id"]] = L, "its leader line points into it"
                    break
        if c["id"] not in seeds and votes:
            L = max(votes, key=lambda L: (votes[L], plan.stats[L][4]))
            seeds[c["id"]], how_seed[c["id"]] = L, "the callout overlaps it (no leader found)"

    # grow every callout's area over neighbours behind a single thin line, all callouts in turn
    claims, regions, conflicts = {}, {}, []
    for c in cos:
        L = seeds.get(c["id"])
        if L is None:
            continue
        if L in claims:
            other = claims[L]
            if type_key(next(o for o in cos if o["id"] == other)) != type_key(c):
                conflicts.append((c["id"], other))
            continue
        claims[L] = c["id"]
        regions[c["id"]] = {L}
    changed = True
    while changed:
        changed = False
        for cid, R in regions.items():
            for L in plan.neighbours(R, r):
                if L in claims or not ok(L):
                    continue
                claims[L] = cid
                R.add(L)
                changed = True
    # then the slivers inside each area's extent (hatching that runs into a wall, a trench along an edge); a strip that
    # reaches beyond the area (the inside of a wall around it) stays out
    extent = {}
    for cid, R in regions.items():  # each area's extent before slivers: they may fill it, never widen it
        st = [plan.stats[L] for L in R]
        extent[cid] = (min(s[0] for s in st) - 2, min(s[1] for s in st) - 2, max(s[0] + s[2] for s in st) + 2,
                       max(s[1] + s[3] for s in st) + 2, sum(int(s[4]) for s in st))
    for _ in range(4):
        changed = False
        for cid, R in regions.items():
            ux0, uy0, ux1, uy1, big = extent[cid]
            for L in plan.neighbours(R, r):
                if L in claims or plan.exterior(L):
                    continue
                x, y, w, h, a = plan.stats[L]
                if x >= ux0 and y >= uy0 and x + w <= ux1 and y + h <= uy1 and a <= 0.05 * big:
                    claims[L] = cid
                    R.add(L)
                    changed = True
        if not changed:
            break

    import cv2
    union = []
    reg_out = {}
    for cid, R in regions.items():
        filled, (ox, oy), cs = plan.outline(R, r)
        px = int(filled.sum())
        area = px / ks ** 2 / 1e6 if ks else None
        ys, xs = np.nonzero(filled)
        bbs = [int(xs.min()) + ox, int(ys.min()) + oy, int(xs.max()) + ox + 1, int(ys.max()) + oy + 1]
        polys = []
        for cnt in sorted(cs, key=cv2.contourArea, reverse=True)[:3]:
            ap = cv2.approxPolyDP(cnt, 1.5, True)[:, 0, :]
            polys.append([[float((x + ox) * f), float((y + oy) * f)] for x, y in ap])
        rect = dict(w=None, h=None)
        inside = []
        if ks:
            for ch in dims["chains"]:
                pts = [(n["bbox"][0] + n["bbox"][2]) / 2 / f - ox for n in ch["items"]], [(n["bbox"][1] + n["bbox"][3]) / 2 / f - oy for n in ch["items"]]
                if not all(0 <= int(y) < filled.shape[0] and 0 <= int(x) < filled.shape[1] and filled[int(y), int(x)] for x, y in zip(*pts)):
                    continue
                inside.append(ch)
                mid = (ch["start"] + ch["end"]) / 2 / f
                cross = ch["cross"] / f
                run = _run(filled, mid - ox, cross - oy, "x") if ch["axis"] == "x" else _run(filled, cross - ox, mid - oy, "y")
                if not run:
                    continue
                span_mm = (run[1] - run[0]) / ks
                ch_rel = abs(span_mm / ch["total"] - 1)
                key = "w" if ch["axis"] == "x" else "h"
                extent = (bbs[2] - bbs[0]) if key == "w" else (bbs[3] - bbs[1])
                if ch["total"] * ks < 0.5 * extent:  # a local dimension (a trench, a sump), not the area's width/depth
                    continue
                if ch_rel <= 0.025 and (rect[key] is None or ch["total"] > rect[key]["total"]):
                    rect[key] = dict(total=ch["total"], values=ch["values"], shown=_chain_text(ch["values"], ch.get("texts")),
                                     measured_mm=round(span_mm), rel=round(ch_rel, 4))
        how_area, area_final, tier = None, area, "measured"
        is_rect = False
        if rect["w"] and rect["h"]:
            W, Hh = rect["w"]["total"], rect["h"]["total"]
            read = W * Hh / 1e6
            bw, bh = (bbs[2] - bbs[0]) / ks, (bbs[3] - bbs[1]) / ks
            is_rect = bool(abs(bw / W - 1) <= 0.025 and abs(bh / Hh - 1) <= 0.025 and abs(area / read - 1) <= 0.03)
            wtxt, htxt = rect["w"]["shown"], rect["h"]["shown"]
            if is_rect:
                area_final, tier = read, "read"
                how_area = (f"{_m(W)} x {_m(Hh)} = {read:.2f} m2 from the printed dimensions inside it "
                            f"(width {wtxt}, depth {htxt}); the traced outline is that rectangle ({area:.2f} m2 measured, "
                            f"{(area / read - 1) * 100:+.1f}%)")
            else:
                how_area = (f"{area:.2f} m2 measured on the traced outline; it is not a plain rectangle: the printed dimensions "
                            f"inside it give a {_m(W)} x {_m(Hh)} = {read:.2f} m2 main part (width {wtxt}, depth {htxt}, "
                            f"both matching the outline within {max(rect['w']['rel'], rect['h']['rel']) * 100:.1f}%), "
                            + (f"and {area - read:.2f} m2 more lies outside that rectangle (traced only, not dimensioned)" if area > read else
                               f"and the outline is {read - area:.2f} m2 smaller than that rectangle (cut-outs or a traced edge short of the dimension)"))
        elif area is not None:
            got = [f"{'width' if a == 'w' else 'depth'} {rect[a]['shown']} printed" for a in ("w", "h") if rect[a]]
            how_area = f"{area:.2f} m2 measured on the traced outline" + (f" ({', '.join(got)} and matching it)" if got else
                                                                         " (no printed dimension inside spans it)")
        else:
            how_area = "outline traced, but no scale was found to turn pixels into metres"
        reg_out[cid] = dict(area_m2=round(area_final, 2) if area_final is not None else None, measured_m2=round(area, 2) if area else None,
                            tier=tier, rectangle=is_rect, read_dims=rect, bbox=[int(v * f) for v in bbs], polygons=polys, how=how_area,
                            components=len(R), dims_inside=[ch["values"] for ch in inside])
        union.append(bbs)

    for key, cs in by_key.items():
        regs, area, missing = [], 0.0, []
        for c in cs:
            if c["id"] in reg_out:
                g = reg_out[c["id"]]
                regs.append(dict(callout=c["id"], **g, assigned=how_seed[c["id"]]))
                if g["area_m2"] is not None:
                    area += g["area_m2"]
            elif c["id"] in seeds:  # its area was already claimed by a callout of the same type
                regs.append(dict(callout=c["id"], shared_with=claims[seeds[c["id"]]], assigned=how_seed[c["id"]]))
            else:
                missing.append(c["id"])
        has = [g for g in regs if g.get("area_m2") is not None]
        res["types"].append(dict(key=key, label=cs[0]["label"], callouts=[c["id"] for c in cs], regions=regs,
                                 area_m2=round(area, 2) if has else None,
                                 tier=("read" if has and all(g["tier"] == "read" for g in has) else "measured") if has else "cannot determine",
                                 unassigned_callouts=missing))
    res["types"].sort(key=lambda t: -(t["area_m2"] or -1))
    res["conflicts"] = conflicts
    res["leaders"] = {k_: dict(tip=[float(v * f) for v in t["tip"]], path=[[float(v * f) for v in p] for p in t["path"]])
                      for k_, t in leaders.items()}

    # cross-check against the overall dimensions
    if k and union:
        for axis in ("x", "y"):
            chs = [c for c in dims["chains"] if c["axis"] == axis]
            if not chs:
                continue
            closed = [d for (c, d) in dims["closures"] if d["axis"] == axis] + [c for (c, d) in dims["closures"] if c["axis"] == axis]
            ov = max(closed or chs, key=lambda c: c["total"])
            lo = min(b[0 if axis == "x" else 1] for b in union) * f
            hi = max(b[2 if axis == "x" else 3] for b in union) * f
            span = (hi - lo) / k
            inside = bool(ov["start"] - 0.02 * k * ov["total"] <= lo and hi <= ov["end"] + 0.02 * k * ov["total"])
            res["checks"].append(dict(axis=axis, overall_mm=ov["total"], overall_chain=ov["values"], slab_span_mm=round(span),
                                      within=inside, left_over_mm=round(ov["total"] - span)))
        xs = [c for c in res["checks"] if c["axis"] == "x"]
        ys = [c for c in res["checks"] if c["axis"] == "y"]
        tot = sum(t["area_m2"] or 0 for t in res["types"])
        res["slab_total_m2"] = round(tot, 2)
        if xs and ys:
            res["envelope_m2"] = round(xs[0]["overall_mm"] * ys[0]["overall_mm"] / 1e6, 2)
        # what lies between neighbouring slab areas (a wall, an isolation joint): its width
        gaps, ids = [], list(reg_out)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                A, B = reg_out[a]["bbox"], reg_out[b]["bbox"]
                for ax, (p0, p1, q0, q1) in (("x", (1, 3, 0, 2)), ("y", (0, 2, 1, 3))):
                    ov = min(A[p1], B[p1]) - max(A[p0], B[p0])
                    if ov <= 0.3 * min(A[p1] - A[p0], B[p1] - B[p0]):
                        continue
                    g = max(B[q0] - A[q1], A[q0] - B[q1]) / k
                    if 0 <= g <= 2000:
                        gaps.append(dict(between=[a, b], axis=ax, gap_mm=round(g)))
        res["gaps"] = gaps
        fp = plan.footprint([((g["bbox"][0] + g["bbox"][2]) / 2 / f, (g["bbox"][1] + g["bbox"][3]) / 2 / f) for g in reg_out.values()], ks)
        if fp:
            res["footprint_m2"] = round(fp[0] / ks ** 2 / 1e6, 2)
            res["footprint_polygons"] = fp[1]
    return res


# ---------------------------------------------------------------------------------------------------------------------
# answer
# ---------------------------------------------------------------------------------------------------------------------


def answer(texts, img=None, m_per_px=None):
    r = areas_by_type(texts, img, m_per_px)
    cos = r["callouts"]
    if not cos:
        return dict(answer=None, unit="m2", by_type=[], evidence=[],
                    explanation="No slab callout (a slab named with a thickness or its bars) was found in this sheet's text, "
                                "so slab types cannot be determined.")
    ev = [dict(id=c["id"], shape="box", bbox=c["bbox"], label=c["text"][:40]) for c in cos]
    parts, lines = [], []
    for i, t in enumerate(r["types"]):
        a = t["area_m2"]
        parts.append(f"{t['label']}: " + (f"{a:.1f} m2" if a is not None else "area not determined"))
        texts_ = "; ".join(f"'{c['text']}'" for c in cos if c["id"] in t["callouts"])
        lines.append(f"Type {i + 1}, {t['label']}. Callout read: {texts_}.")
        for g in t["regions"]:
            if "polygons" in g:
                for j, poly in enumerate(g["polygons"]):
                    ev.append(dict(id=f"{g['callout']}_area{j}", shape="polygon", pts=poly, label=f"{a:.1f} m2" if a else t["label"][:20]))
                lines.append(f"  Area: which part of the plan it covers is estimated ({g['assigned']}; that enclosed area was "
                             f"grown across thin lines such as control joints and dimension lines, stopping at walls/joints drawn "
                             f"as bands). {g['how']}.")
            elif "shared_with" in g:
                lines.append(f"  Callout {g['callout']} points into the same area as {g['shared_with']} (counted once).")
        if t["unassigned_callouts"] and img is not None:
            lines.append(f"  {len(t['unassigned_callouts'])} callout(s) of this type could not be tied to an area: none of the "
                         "enclosed areas wider than 0.5 m holds it and no leader line from it was found"
                         + (" (that width test depends on the scale, which is in doubt: see Scale)" if r["scale"].get("warning") else "") + ".")
        if img is None:
            lines.append("  " + t["how"] + ".")
    sc = r["scale"]
    if sc["px_per_mm"]:
        lines.append(f"Scale: {sc['source']}" + (f"; {sc['printed_check']}" if sc.get("printed_check") else "") + "."
                     + (f" Warning: {sc['warning']}." if sc.get("warning") else ""))
    else:
        lines.append("Scale: none found (no consistent dimension chain, no scale given), so no area can be put in m2.")
    if r["closures"]:
        cl = "; ".join(f"{'horizontal' if c['axis'] == 'x' else 'vertical'} " + " = ".join(sorted(c["shown"], key=lambda t: (len(t), t)))
                       for c in r["closures"][:6])
        lines.append(f"Dimension chains read and checked: {cl}.")
    for c in r["checks"]:
        lines.append(f"Cross-check ({'horizontal' if c['axis'] == 'x' else 'vertical'}): the slab areas span {c['slab_span_mm'] / 1000:.2f} m "
                     f"of the {_m(c['overall_mm'])} overall dimension"
                     + (f"; the {c['left_over_mm'] / 1000:.2f} m left is walls and joints between and around them" if c["within"] and c["left_over_mm"] >= 0
                        else " — they extend beyond it: check the traced outlines") + ".")
    if r.get("gaps"):
        name = {c["id"]: c["label"].split(",")[0] + (", " + c["bars"][0]["bar"] if c["bars"] else "") for c in cos}
        lines.append("Between neighbouring slab areas: " + "; ".join(
            f"{g['gap_mm'] / 1000:.2f} m ({name[g['between'][0]]} | {name[g['between'][1]]})" for g in r["gaps"]) + " (walls / joints, not slab).")
    if r.get("footprint_m2"):
        share = r["slab_total_m2"] / r["footprint_m2"] * 100
        lines.append(f"Building outline traced inside the overall dimensions: {r['footprint_m2']:.1f} m2 (the {r.get('envelope_m2', 0):.1f} m2 "
                     f"rectangle of the overall dimensions minus its notches); the slab areas add up to {r['slab_total_m2']:.1f} m2 = "
                     f"{share:.0f}% of it, the rest being walls and whatever is drawn against them (pits, pads, steps).")
    elif r.get("envelope_m2"):
        lines.append(f"All slab areas add up to {r['slab_total_m2']:.1f} m2 inside the {r['envelope_m2']:.1f} m2 rectangle of the "
                     f"overall dimensions (the rest is walls, joints and the parts of that rectangle outside the building).")
    if r.get("conflicts"):
        lines.append(f"{len(r['conflicts'])} pair(s) of callouts of different types fall in one undivided area; that area is "
                     "counted for the first only.")
    lines.append("Read: callout words (thickness, bars, spacing, faces, top of slab) and dimension numbers"
                 + (" (an area given as W x H is read from them)" if any(t.get("tier") == "read" for t in r["types"]) else "") + ". "
                 "Estimated: which outline each callout covers. Measured: traced outline areas at the scale above.")
    known = [t for t in r["types"] if t["area_m2"] is not None]
    return dict(answer="; ".join(parts), unit="m2", by_type=[dict(type=t["label"], area_m2=t["area_m2"], tier=t["tier"]) for t in r["types"]],
                total_m2=round(sum(t["area_m2"] for t in known), 2) if known else None,
                explanation="\n".join(lines), evidence=ev, detail=r)
