"""Tags, sizes, lines and printed 'label = value' pairs, read from a sheet's text.

Every function takes `texts`, the sheet's list of {text, bbox: [x0, y0, x1, y1], ...} in sheet coordinates: OCR of an
image (drawqa/textlayer.py) or a vector PDF's text layer (pdfsheet.load(...)["texts"]). They do not care which, and
tolerate OCR slips (O/0, I/1, a missing hyphen "PIT1", one word read twice by overlapping tiles). Pure functions.

    lines(texts)               -> [dict(text, bbox, ids, vertical)]    boxes on one baseline merged into text lines
    find(texts, words)         -> [dict(text, bbox, ids, kind, score)] lines (else text blocks) holding all the words
    definitions(texts)         -> {ABBR: [dict(text, kind, statement, stated_count, bbox, ids)]}  as the sheet states them
    inventory(texts)           -> {PREFIX: dict(tags, numbers, count, placements, occurrences, gaps, definition, ...)}
    count(texts, prefix)       -> dict(answer, unit="count", tags, gaps, explanation, evidence, ...)
    pipe_sizes(texts, about)   -> [dict(size, inches, mm, subject, kind, text, bbox, ids)]
    size_of(texts, words)      -> dict(answer, unit, explanation, evidence) | None   "what size is the suction header?"
    key_values(texts)          -> [dict(label, value, unit, raw, bbox, ids)]   "WEST WING DORMITORY = 6,095 SF"

What counts as an equipment tag: PREFIX-n (CV-1, FSCV-7, M-301A), PREFIX n (PIT 1) and PREFIXn (PIT1, MFR35) when
it stands as its own label (not inside a sentence) and the prefix is used as a tag family on the sheet. Not tags:
sheet references (a section bubble's lower half A1/M-302, "SEE SHEET S-101", the sheet's own number and every other
number of the same family), spec references (UFC 3-460-01, MSS SP-110), single-letter codes without a hyphen (grid
and detail ids A6, rebar D16, finish codes F1), dimensions, years, and words like NOTE 1 or LEVEL 1.
"""
import re
from bisect import bisect_left, bisect_right
from difflib import SequenceMatcher

GAP = 1.0         # boxes on one baseline closer than this many text heights form one line
ROW_GAP = 15      # a printed value up to this many text heights right of its label belongs to it
LEGEND_GAP = 8    # an abbreviation and its meaning on one legend row

STOP = set("""NOTE NOTES SHEET SHEETS SHT DETAIL DETAILS SECTION SECT ELEV ELEVATION VIEW PLAN PLANS TYPE TYPES LEVEL LVL
FLOOR FLR PHASE STAGE AREA ZONE WING BLOCK PLOT UNIT FLAT ROOM RM BED BAY CORE STAIR LIFT GRID LINE AXIS OPTION ITEM PAGE
REV ISSUE NO NR NUM SCALE KEY FIG TABLE CLASS GRADE SCH SDR DN NPS PN GA PSI PSIG GPM CFM KW HP KVA MM CM IN FT SF LF
EL TYP SIM OF AND TO FROM AT ON BY FOR WITH SEE THE OR PER X R U K MIN MAX EA QTY TOTAL TANK DAY WEEK YEAR STEP PART
DIV CHAPTER DRAWING DWG ADDENDUM APPENDIX ANNEX REF VOL
JAN FEB MAR APR MAY JUN JUL AUG SEP SEPT OCT NOV DEC JUNE JULY MARCH APRIL
UFC UFGS ASTM ASME ANSI NFPA AWWA MSS ISO EN DIN BS CSA UL IBC IPC IMC NEC CISPI ASHRAE SMACNA API AISC ACI AWS CPSC
CFR NSF IEC IEEE""".split())
SPEC = re.compile(r"\b(?:UFC|UFGS|ASTM|ASME|ANSI|NFPA|AWWA|MSS|ISO|EN|DIN|BS|CSA|UL|IBC|IPC|IMC|NEC|CISPI|ASHRAE|SMACNA|API|"
                  r"AISC|ACI|AWS|CPSC|CFR|NSF|IEC|IEEE)\s*$")
SHEET_CTX = re.compile(r"(?:\bSHEETS?|\bSHT\.?|\bDWG\.?|\bDRAWINGS?|\bSEE|\bREF\.?|\bREFER\s+TO)\s*(?:NO\.?)?\s*[:#]?\s*$")
DETAIL_ID = re.compile(r"^(?:[A-Z]{1,2}\d{1,2}|\d{1,2}[A-Z]?|[A-Z])$")
UNIT_AFTER = re.compile(r"""^\s?(?:["'″′”]|(?:MM|CM|IN|FT|SF|SQ|LF|GA|LBS?|PSIG?|GPM|CFM|KG|KN|KW|KVA|HZ|DEG)\b|%|°|\s*[Xx×]\s*\d)""")
# PREFIX + separator + number (+ one suffix letter).  OCR may print 0/1 as O/I/l inside a hyphenated number.
TAG = re.compile(r"(?<![A-Za-z0-9\-])([A-Z]{1,5})(-|–|\s|)((?<=[-–])[0-9OIl|]{0,3}[0-9][0-9OIl|]{0,3}|(?<![-–])\d{1,4})"
                 r"([A-Z])?(?![A-Za-z0-9])(?!\.\d)")
LIST_MORE = re.compile(r"\s*(?:&|,|\+|AND)\s*(\d{1,3})(?![\d\"'.A-Za-z])")


# ---------------------------------------------------------------- boxes -> lines -> blocks

def _orient(text, b):
    w, h = b[2] - b[0], b[3] - b[1]
    n = len(text.replace(" ", ""))
    if n >= 3:
        return "v" if h > 1.2 * w else "h"
    if n == 2:
        return "v" if h > 1.4 * w else "h" if w > 1.2 * h else None
    return None


def _items(texts):
    """Text boxes with a reading frame: f = (u0, v0, u1, v1), u along the text, v across it. Vertical text on drawings
    reads bottom to top, so for it u = -y and v = x. Boxes too short to show their direction follow the sheet."""
    its = []
    for i, t in enumerate(texts):
        s = " ".join(str(t.get("text") or "").split())
        b = t.get("bbox")
        if not s or not b or len(b) < 4:
            continue
        b = [min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])]
        its.append(dict(id=t.get("id", f"t{i}"), text=s, bbox=b, o=_orient(s, b)))
    nv, nh = sum(it["o"] == "v" for it in its), sum(it["o"] == "h" for it in its)
    default = "v" if nv >= 3 and nv > 1.5 * nh else "h"
    for it in its:
        it["vert"] = (it.pop("o") or default) == "v"
        x0, y0, x1, y1 = it["bbox"]
        it["f"] = (-y1, x0, -y0, x1) if it["vert"] else (x0, y0, x1, y1)
        it["th"] = max(1e-6, it["f"][3] - it["f"][1])
    return its


def _same_text(a, b):
    """b is (about) contained in a: the same word read twice."""
    a, b = a.upper(), b.upper()
    if b in a:
        return True
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size >= max(2, 0.7 * len(b))


def _new_run(it):
    return dict(text=it["text"], segs=[(0, len(it["text"]), it)], f=list(it["f"]), vert=it["vert"], th=it["th"],
                last=it, dups=[])


def _append(run, it):
    a, b = run["text"], it["text"]
    last = run["last"]
    lf, f = last["f"], it["f"]
    m = min(last["th"], it["th"])
    if f[2] <= lf[2] + 0.2 * m:  # inside the last box: the same word read twice
        run["dups"].append(it)
        return
    gap = f[0] - lf[2]
    if gap >= -0.1 * m:
        sep = "" if gap < 0.05 * m and a[-1:].isalnum() and b[:1].isalnum() else " "  # one word split in two spans
        start, b0, text = len(a) + len(sep), 0, a + sep + b
    else:  # two overlapping reads (OCR tiles) of one line: join them where they share text
        n = int(len(last["text"]) * min(1.0, -gap / max(1.0, lf[2] - lf[0]))) + 3
        tail, head = a[-n:], b[:n + 3]
        mt = SequenceMatcher(None, tail, head, autojunk=False).find_longest_match(0, len(tail), 0, len(head))
        if mt.size >= 3 or (mt.size >= 2 and mt.size == len(b)):
            cut = len(a) - len(tail) + mt.a
            run["segs"] = [(s, min(e, cut), x) for s, e, x in run["segs"] if s < cut]
            start, b0, text = cut, mt.b, a[:cut] + b[mt.b:]
        else:
            start, b0, text = len(a) + 1, 0, a + " " + b
    run["segs"].append((start, start + len(b) - b0, it))
    run["text"], run["last"] = text, it
    run["f"] = [min(run["f"][0], f[0]), min(run["f"][1], f[1]), max(run["f"][2], f[2]), max(run["f"][3], f[3])]


def _finish(run):
    its = [x for _, _, x in run["segs"]] + run.get("dups", [])
    run["bbox"] = [min(x["bbox"][0] for x in its), min(x["bbox"][1] for x in its),
                   max(x["bbox"][2] for x in its), max(x["bbox"][3] for x in its)]
    run["ids"] = [x["id"] for x in its]
    run["th"] = sum(x["th"] for _, _, x in run["segs"]) / len(run["segs"])
    return run


def _lines(items):
    out = []
    for vert in (False, True):
        live = []
        for it in sorted((i for i in items if i["vert"] == vert), key=lambda i: (i["f"][0], i["f"][1])):
            f, th = it["f"], it["th"]
            best, bgap, keep = None, None, []
            for L in live:
                lf, lth = L["last"]["f"], L["last"]["th"]
                if f[0] - lf[2] > 3 * GAP * lth:
                    out.append(L)  # nothing further right can join this line
                    continue
                keep.append(L)
                m = min(th, lth)
                if not 0.6 <= th / lth <= 1.67 or min(f[3], lf[3]) - max(f[1], lf[1]) < 0.5 * m:
                    continue
                gap = f[0] - lf[2]
                if gap > GAP * m:
                    continue
                if f[2] <= lf[2] + 0.2 * m and not _same_text(L["last"]["text"], it["text"]):
                    continue  # a different word inside the last box: not part of this line
                if best is None or abs(gap) < abs(bgap):
                    best, bgap = L, gap
            live = keep
            if best is None:
                live.append(_new_run(it))
            else:
                _append(best, it)
        out += live
    return [_finish(L) for L in out]


def _blocks(lines):
    """Lines stacked into paragraphs (a note, a leader label written on three lines)."""
    out = []
    for vert in (False, True):
        live = []
        for L in sorted((l for l in lines if l["vert"] == vert), key=lambda l: (l["f"][1], l["f"][0])):
            f, th = L["f"], L["th"]
            best, bg, keep = None, None, []
            for B in live:
                P = B["lines"][-1]
                pf = P["f"]
                if f[1] - pf[3] > 3 * P["th"]:
                    out.append(B)
                    continue
                keep.append(B)
                m = min(th, P["th"])
                vgap = f[1] - pf[3]
                if not -0.35 * m <= vgap <= 1.0 * m or not 0.7 <= th / P["th"] <= 1.43:
                    continue
                uov = min(f[2], pf[2]) - max(f[0], pf[0])
                if abs(f[0] - pf[0]) > 1.5 * m and uov < 0.5 * min(f[2] - f[0], pf[2] - pf[0]):
                    continue
                if best is None or vgap < bg:
                    best, bg = B, vgap
            live = keep
            if best is None:
                live.append(dict(lines=[L], vert=vert))
            else:
                best["lines"].append(L)
        out += live
    for B in out:
        text, segs, offs = "", [], []
        for L in B["lines"]:
            if text:
                text += "" if text.endswith("-") and L["text"][:1].isalpha() else " "
            offs.append(len(text))
            segs += [(s + len(text), e + len(text), x) for s, e, x in L["segs"]]
            text += L["text"]
            L["block"] = B
        B.update(text=text, segs=segs, offs=offs, dups=[d for L in B["lines"] for d in L["dups"]])
        B["f"] = [min(L["f"][0] for L in B["lines"]), min(L["f"][1] for L in B["lines"]),
                  max(L["f"][2] for L in B["lines"]), max(L["f"][3] for L in B["lines"])]
        _finish(B)
        for L, o in zip(B["lines"], offs):
            L["boff"] = o
    return out


def _layout(texts):
    items = _items(texts)
    L = _lines(items)
    B = _blocks(L)
    return items, L, B


def _span(run, a, b):
    """Boxes (bbox, ids) of the characters a..b of a line or block."""
    its = [x for s, e, x in run["segs"] if s < b and e > a] or [x for _, _, x in run["segs"]]
    bb = [min(x["bbox"][0] for x in its), min(x["bbox"][1] for x in its),
          max(x["bbox"][2] for x in its), max(x["bbox"][3] for x in its)]
    return [round(v, 1) for v in bb], [x["id"] for x in its], its


def _out(run, kind=None):
    d = dict(text=run["text"], bbox=[round(v, 1) for v in run["bbox"]], ids=run["ids"], vertical=run["vert"])
    if kind:
        d["kind"] = kind
    return d


def lines(texts):
    """Text boxes on one baseline merged into lines (reading order: top to bottom, left to right)."""
    _, L, _ = _layout(texts)
    L.sort(key=lambda l: (l["vert"], round(l["bbox"][1] / max(1.0, l["th"])), l["bbox"][0]))
    return [_out(l) for l in L]


# ---------------------------------------------------------------- find

def _tokens(s):
    return re.findall(r"[A-Z0-9][A-Z0-9\"'./&-]*", s.upper())


def _word_score(w, toks):
    best = 0.0
    for t in toks:
        t2 = t.strip(".,:;()")
        if t2 == w:
            return 1.0
        if len(w) >= 4 and w in t2:
            best = max(best, 0.95)
            continue
        if len(w) <= 3 or len(t2) < 3:
            # short words: only OCR look-alikes may differ
            if len(t2) == len(w) and all(a == b or {a, b} in ({"O", "0"}, {"I", "1"}, {"S", "5"}, {"B", "8"}, {"Z", "2"})
                                         for a, b in zip(w, t2)):
                best = max(best, 0.9)
            continue
        r = SequenceMatcher(None, w, t2, autojunk=False).ratio()
        if r >= 0.8:  # one slip in five letters; WEST is not EAST
            best = max(best, r)
    return best


def find(texts, words, limit=50):
    """Lines that hold every one of `words` (tolerant to OCR typos), best first; when no single line holds them all,
    the text blocks (a label written over several lines) that do. Each: dict(text, bbox, ids, kind, score)."""
    if isinstance(words, str):
        words = words.split()
    q = [w for w in (_tokens(" ".join(words))) if w]
    if not q:
        return []
    _, L, B = _layout(texts)
    for runs, kind in ((L, "line"), (B, "block")):
        hits = []
        for r in runs:
            toks = _tokens(r["text"])
            sc = [_word_score(w, toks) for w in q]
            if all(s > 0 for s in sc):
                hits.append((sum(sc) / len(sc), r))
        if kind == "block":
            hits = [(s, r) for s, r in hits if len(r["lines"]) > 1]
        if hits:
            hits.sort(key=lambda h: (-h[0], h[1]["bbox"][1], h[1]["bbox"][0]))
            return [dict(_out(r, kind), score=round(s, 3)) for s, r in hits[:limit]]
    return []


# ---------------------------------------------------------------- definitions

ABBR = re.compile(r"^[A-Z][A-Z0-9.&/-]{0,7}$")
PAREN = re.compile(r"\(([A-Z][A-Z0-9&/-]{0,7})\)")
EQUALS = re.compile(r"^[•·*\-\s]*\(?([A-Z][A-Z0-9.&/-]{0,7})\)?\s*(?:=|\s[-–]\s)\s*([A-Za-z].{2,})$")
STATED = re.compile(r"\bTYP(?:ICAL)?\.?\s*(?:OF\s*)?\(?(\d{1,3})\)?|\((\d{1,3})\)\s*(?:REQUIRED|REQ'?D|TOTAL)\b|"
                    r"\b(\d{1,3})\s*(?:REQUIRED|REQ'?D)\b|\bQTY\.?\s*[:=]?\s*(\d{1,3})\b")
PAREN_STOP = {"TYP", "SIM", "NIC", "ETC", "EX", "E", "N", "NEW", "EXIST", "OR", "S", "SEE"}


def _stated(s):
    m = STATED.search(s)
    return int(next(g for g in m.groups() if g)) if m else None


def _row_index(items):
    """Boxes sorted by the middle of their row, per orientation, for quick same-row lookups."""
    idx = {}
    for vert in (False, True):
        its = sorted((i for i in items if i["vert"] == vert), key=lambda i: (i["f"][1] + i["f"][3]) / 2)
        idx[vert] = ([(i["f"][1] + i["f"][3]) / 2 for i in its], its)
    return idx


def _row_neighbour(it, idx, maxgap, right=True):
    """The nearest box on the same row after (right=True) or before `it` within maxgap text heights."""
    f, th = it["f"], it["th"]
    keys, its = idx[it["vert"]]
    lo, hi = bisect_left(keys, f[1] - th), bisect_right(keys, f[3] + th)
    best, bg = None, None
    for o in its[lo:hi]:
        if o is it or o["vert"] != it["vert"]:
            continue
        g = o["f"]
        m = min(th, o["th"])
        if min(f[3], g[3]) - max(f[1], g[1]) < 0.5 * m:
            continue
        gap = g[0] - f[2] if right else f[0] - g[2]
        if -0.2 * m <= gap <= maxgap * m and (bg is None or gap < bg):
            best, bg = o, gap
    return best


def _is_words(s):
    letters = sum(c.isalpha() for c in s)
    return (s[:1].isalpha() and letters >= 4 and letters >= 0.6 * len(s.replace(" ", ""))
            and not (" " not in s.strip() and re.search(r"\d", s)))


def _definitions(items, L, B):
    out = {}

    def add(abbr, **d):
        lst = out.setdefault(abbr, [])
        if not any(x["text"] == d["text"] for x in lst):
            lst.append(d)

    for blk in B:
        s = blk["text"]
        for m in PAREN.finditer(s):
            abbr = m.group(1)
            if abbr in PAREN_STOP or not re.search(r"[A-Z]", abbr):
                continue
            after = s[m.end():]
            eq = re.match(r"\s*=\s*([^=]{3,120}?)(?=\s*\([A-Z0-9]{1,8}\)\s*=|$)", after)
            if eq:  # "(A6) = 6" METAL STUD ..."
                text = eq.group(1).strip()
            else:  # the words before it on its own line; the lines above only when it starts its line
                ln = next(l for l, o in zip(blk["lines"][::-1], blk["offs"][::-1]) if o <= m.start())
                lo = blk["offs"][blk["lines"].index(ln)]
                own_line = s[lo:m.start()]
                head = own_line if sum(c.isalpha() for c in own_line) >= 3 else s[:m.start()]
                before = re.split(r"[.;:!?]\s|\d+\.\s|\)\s", head)[-1].strip(" ,-")
                words = before.split()
                if not words or sum(c.isalpha() for c in before) < 3:
                    continue
                text = " ".join(words[-8:])
            bb, ids, _ = _span(blk, max(0, m.start() - len(text) - 2), m.end() + (len(eq.group(0)) if eq else 0))
            add(abbr, text=text, kind="parenthesis", statement=s[:240], stated_count=_stated(after), bbox=bb, ids=ids)
    for ln in L:
        m = EQUALS.match(ln["text"])
        dash = m and "=" not in ln["text"][:m.start(2)]
        if m and m.group(1) not in STOP and _is_words(m.group(2).strip()) and not (
                dash and (len(m.group(1)) > 5 or re.search(r"\s[-–]\s", m.group(2)))):
            add(m.group(1), text=m.group(2).strip(), kind="equals", statement=ln["text"],
                stated_count=_stated(m.group(2)), bbox=[round(v, 1) for v in ln["bbox"]], ids=ln["ids"])
    # legend rows: an abbreviation box, its meaning in the next box on the row, and at least one more row like it
    pairs, idx = [], _row_index(items)
    for it in items:
        if ABBR.match(it["text"]) and re.search(r"[A-Z]", it["text"]) and it["text"] not in STOP and not re.match(r"^[A-Z]\.$", it["text"]):
            nb = _row_neighbour(it, idx, LEGEND_GAP)
            if nb is not None and _is_words(nb["text"]) and not nb["text"].rstrip().endswith(":"):
                pairs.append((it, nb))
    for it, nb in pairs:
        m = min(it["th"], nb["th"])
        if any(o is not it and abs(o["f"][0] - it["f"][0]) <= 0.8 * m and abs(onb["f"][0] - nb["f"][0]) <= 0.8 * m
               for o, onb in pairs):
            bb = [min(it["bbox"][0], nb["bbox"][0]), min(it["bbox"][1], nb["bbox"][1]),
                  max(it["bbox"][2], nb["bbox"][2]), max(it["bbox"][3], nb["bbox"][3])]
            add(it["text"].rstrip("."), text=nb["text"], kind="legend", statement=f"{it['text']}  {nb['text']}",
                stated_count=None, bbox=[round(v, 1) for v in bb], ids=[it["id"], nb["id"]])
    return out


def definitions(texts):
    """What the sheet itself says an abbreviation means: 'NON-SURGE CHECK VALVE (CV) ...', 'CV = CHECK VALVE', legend
    rows ('LAV | LAVATORY'). {ABBR: [dict(text, kind, statement, stated_count, bbox, ids)]}."""
    return _definitions(*_layout(texts))


# ---------------------------------------------------------------- tags

def _num(s):
    return int(s.translate(str.maketrans("OIl|", "0111")))


def _sheet_ids(items):
    """The sheet's own number(s): the tag-like text in the title block (next to 'SHEET ID' / 'SHEET NUMBER', or in a
    far larger type than the rest of the sheet)."""
    tagish = re.compile(r"^[A-Z]{1,3}-?\d{1,4}(?:\.\d+)?[A-Z]?$")
    ths = sorted(it["th"] for it in items if len(it["text"]) >= 2)
    med = ths[len(ths) // 2] if ths else 0
    labels = [it for it in items if re.match(r"^(?:SHEET|DRAWING|DWG)\b|^(?:SHEET\s*)?(?:NUMBER|NO\.?)\s*:?$", it["text"].upper())]
    ids = set()
    for it in items:
        if not tagish.match(it["text"]):
            continue
        if med and it["th"] >= 2.5 * med:
            ids.add(it["text"])
            continue
        cx, cy = (it["bbox"][0] + it["bbox"][2]) / 2, (it["bbox"][1] + it["bbox"][3]) / 2
        for lb in labels:
            r = 6 * lb["th"]
            if lb["bbox"][0] - r <= cx <= lb["bbox"][2] + r and lb["bbox"][1] - r <= cy <= lb["bbox"][3] + r and it["th"] >= lb["th"]:
                ids.add(it["text"])
    return ids


def _bubble(it, items):
    """A whole-box sheet number with a short detail id right above or below it (the two halves of a section or detail
    bubble: A1 over M-302)."""
    f, th = it["f"], it["th"]
    for o in items:
        if o is it or o["vert"] != it["vert"] or not DETAIL_ID.match(o["text"]) or '"' in o["text"]:
            continue
        g = o["f"]
        cu = (g[0] + g[2]) / 2
        if not f[0] - 0.3 * th <= cu <= f[2] + 0.3 * th:
            continue
        if -0.3 * th <= f[1] - g[3] <= 1.2 * th or -0.3 * th <= g[1] - f[3] <= 1.2 * th:
            return True
    return False


def _scan(texts):
    items, L, B = _layout(texts)
    defs = _definitions(items, L, B)
    own = _sheet_ids(items)
    cands = []
    for ln in L:
        blk = ln["block"]
        o = ln["boff"]
        every = [m.group(0) for m in TAG.finditer(ln["text"])]
        for m in TAG.finditer(ln["text"]):
            prefix, sep, num, suf = m.group(1), m.group(2), m.group(3), m.group(4) or ""
            sep = "-" if sep in "-–" and sep else (" " if sep else "")
            before, after = blk["text"][:o + m.start()], blk["text"][o + m.end():]
            more, end = [], m.end()
            for mm in LIST_MORE.finditer(ln["text"], m.end()):
                if mm.start() != end:
                    break
                more.append(mm.group(1))
                end = mm.end()
            bb, ids, its = _span(ln, m.start(), end)
            # a label when the tag (with its list) is all its boxes say, give or take "(TYP.)"
            rest = "".join(x["text"] for x in its)
            for x in every + more:  # other tags on the same label ("PIT 1, PIT 2") do not make it a sentence
                rest = rest.replace(x, "", 1)
            rest = re.sub(r"\(?\b(?:TYP|SIM|TYPICAL)\b\.?\)?|AND|[^A-Za-z0-9]", "", rest)
            raw = m.group(0) if not more else ln["text"][m.start():end]
            c = dict(prefix=prefix, sep=sep, num=num, suf=suf, numbers=[num] + more, raw=raw, bbox=bb, ids=ids,
                     line=ln["text"], label=len(rest) <= 4, why=None, its=its,
                     single=len(its) == 1 and its[0]["text"].strip() == m.group(0))
            ctx = before[-24:]
            if prefix in STOP:
                c["why"] = "word, not a tag prefix"
            elif SPEC.search(before[-12:].rstrip() + " ") or SPEC.search(before.rstrip()):
                c["why"] = "spec reference"
            elif UNIT_AFTER.match(after) or UNIT_AFTER.match(ln["text"][m.end():]):
                c["why"] = "dimension or quantity"
            elif re.sub(r"\s", "", m.group(0)) in own or m.group(0) in own:
                c["why"] = "sheet number (title block)"
            elif SHEET_CTX.search(ctx):
                c["why"] = "sheet reference"
            elif re.search(r"(?:^|\s)(?:[A-Z]{1,2}\d{1,2}|\d{1,2}[A-Z]?)\s*/\s*$", before):
                c["why"] = "sheet reference"
            elif c["label"] and len(prefix) <= 2 and len(num) >= 2 and _bubble(its[0], items):
                c["why"] = "sheet reference"
            elif sep != "-" and len(num) > 3:
                c["why"] = "number, not a tag"
            elif sep != "-" and len(prefix) == 1:
                c["why"] = "single letter code (grid, detail, rebar or finish id)"
            elif sep == "" and len(set(prefix)) == 1 and prefix in "IL":
                c["why"] = "OCR of line work"
            cands.append(c)
    # a sheet-reference family (S-5xx, M-3xx; the sheet's own number) marks every member as a reference
    fam = lambda c: (c["prefix"], len(c["num"]), c["sep"])
    refs = {fam(c) for c in cands if c["why"] in ("sheet reference", "sheet number (title block)")}
    for t in own:
        m = re.match(r"^([A-Z]{1,3})(-?)(\d{1,4})", t)
        if m:
            refs.add((m.group(1), len(m.group(3)), "-" if m.group(2) else ""))
    for c in cands:
        if c["why"] is None and fam(c) in refs:
            c["why"] = "sheet reference"
    # prefixes printed with a hyphen as labels are tag families; spaced or unspaced forms need that, or repetition
    ok = [c for c in cands if c["why"] is None]
    hyph = {c["prefix"] for c in ok if c["sep"] == "-" and c["label"]}
    spaced = {}
    for c in ok:
        if c["sep"] == " " and c["label"] and 2 <= len(c["prefix"]) <= 5:
            spaced[c["prefix"]] = spaced.get(c["prefix"], 0) + 1
    confirmed = hyph | {p for p, n in spaced.items() if n >= 2}
    tok_n = {}
    for c in ok:
        if c["sep"] == "" and c["label"]:
            k = c["prefix"] + c["num"]
            tok_n[k] = tok_n.get(k, 0) + 1
    for c in ok:
        if c["prefix"] in confirmed or c["sep"] == "-":
            continue
        if c["sep"] == "" and c["label"] and len(set(c["prefix"])) >= 2 and len(c["prefix"]) >= 2 and (
                tok_n.get(c["prefix"] + c["num"], 0) >= 2 or sum(1 for d in ok if d["prefix"] == c["prefix"] and d["label"]) >= 3):
            continue
        c["why"] = "not used as a tag family on this sheet"
    return items, L, B, defs, own, cands


def _ranges(nums, fmt):
    nums = sorted(set(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(fmt(nums[i]) if j == i else (f"{fmt(nums[i])}, {fmt(nums[j])}" if j == i + 1 else f"{fmt(nums[i])}..{fmt(nums[j])}"))
        i = j + 1
    return ", ".join(out)


def _gaps(nums):
    """Numbers missing from a 1..n (or per-hundred 101..1nn) sequence; (gaps, is_sequence)."""
    nums = sorted(set(nums))
    if len(nums) < 2:
        return [], True
    S = set(nums)
    groups = {}
    if nums[0] >= 100 and len({n // 100 for n in nums}) > 1:
        for n in nums:
            groups.setdefault(n // 100, []).append(n)
    else:
        groups[0] = nums
    miss = []
    for g, ns in groups.items():
        lo = ns[0]
        if lo - g * 100 <= 9:
            lo = min(lo, g * 100 + 1)
        if ns[-1] - lo > 500:
            return [], False
        miss += [n for n in range(lo, ns[-1]) if n not in S]
    return miss, len(miss) <= max(2, len(nums))


def inventory(texts):
    """Equipment tags on the sheet grouped by prefix. {PREFIX: dict(
        prefix, form ('-', ' ' or ''), tags [canonical, number order], numbers, count (distinct tags),
        placements (labels on the sheet), repeats {tag: n} for tags labelled more than once,
        occurrences [dict(tag, number, read, bbox, ids, line, role 'label'|'legend')], unnumbered [bare 'PS' labels],
        mentions [tags named inside notes, not counted], gaps [tags missing from the sequence], gap_note,
        definition (the sheet's own words for the prefix, or None), tag_definitions {tag: text})}"""
    return _inventory(*_scan(texts))


def _inventory(items, L, B, defs, own, cands):
    fams = {}
    for c in cands:
        if c["why"] is None:
            fams.setdefault(c["prefix"], []).append(c)
    inv = {}
    for p, cs in sorted(fams.items()):
        forms = {}
        for c in cs:
            forms[c["sep"]] = forms.get(c["sep"], 0) + 1
        form = "-" if forms.get("-") else max(forms, key=forms.get)
        name = lambda n, s="": f"{p}{form}{n}{s}"
        occ, men, seen = [], [], []
        for c in cs:
            for k, nraw in enumerate(c["numbers"]):
                n = _num(nraw)
                tag = name(n, c["suf"] if k == 0 else "")
                o = dict(tag=tag, number=n, read=c["raw"], bbox=c["bbox"], ids=c["ids"], line=c["line"])
                if not c["label"]:
                    men.append(o)
                    continue
                if any(t == tag and _overlap(b, c["bbox"]) for t, b in seen):
                    continue  # the same label read twice
                seen.append((tag, c["bbox"]))
                legend = tag in defs and any(d["kind"] == "legend" and set(d["ids"]) & set(c["ids"]) for d in defs[tag])
                o["role"] = "legend" if legend else "label"
                occ.append(o)
        if not occ:
            continue
        labels = [o for o in occ if o["role"] == "label"]
        tags = sorted({o["tag"] for o in occ}, key=lambda t: (int(re.search(r"\d+", t[len(p):]).group()), t))
        nums = sorted({o["number"] for o in occ})
        gaps, seq = _gaps(nums)
        rep = {}
        for o in labels:
            rep[o["tag"]] = rep.get(o["tag"], 0) + 1
        rep = {t: n for t, n in sorted(rep.items(), key=lambda kv: -kv[1]) if n > 1}
        bare = [ln for ln in L if ln["text"].rstrip("-– ") == p and len(ln["text"]) <= len(p) + 1]
        d = (defs.get(p) or [None])[0]
        if seq:
            note = (", ".join(name(g) for g in gaps) + " not found: not read, or not used on this sheet") if gaps else ""
        else:
            note = f"numbers are not one sequence ({_ranges(nums, lambda n: name(n))}); missing numbers are not listed"
        corr = sorted({(o["read"], o["tag"]) for o in occ if re.search(r"[OIl|]", o["read"][len(p):])})
        inv[p] = dict(prefix=p, form=form, tags=tags, numbers=nums, count=len(tags), placements=len(labels),
                      repeats=rep, occurrences=occ, unnumbered=[dict(bbox=ln["bbox"], ids=ln["ids"]) for ln in bare],
                      mentions=men, gaps=[name(g) for g in gaps] if seq else [], gap_note=note, sequence=seq,
                      definition=d, tag_definitions={t: defs[t][0]["text"] for t in tags if t in defs},
                      corrections=[f"read '{r}' as {t}" for r, t in corr])
    return inv


def _overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def excluded(texts):
    """Tag-like texts that were not counted, with the reason (for checking the exclusion rules)."""
    *_, cands = _scan(texts)
    return [dict(read=c["raw"], why=c["why"], line=c["line"], bbox=c["bbox"]) for c in cands if c["why"]]


def _singular(w):
    return w[:-2] if w.endswith("ES") and len(w) > 4 and w[-3] in "SXZH" else w[:-1] if w.endswith("S") and len(w) > 3 else w


def _resolve(inv, defs, what):
    q = what.strip().upper()
    key = re.sub(r"[-–\s]+$", "", q)
    if key in inv:
        return key
    if key.replace(" ", "") in inv:
        return key.replace(" ", "")
    words = [_singular(w) for w in re.findall(r"[A-Z]{2,}", q)]
    best, bs = None, 0.0
    for p in inv:
        for d in [d for k in [p] + inv[p]["tags"] for d in defs.get(k, [])]:
            toks = [_singular(t) for t in _tokens(d["text"])]
            sc = [_word_score(w, toks) for w in words]
            if sc and all(s > 0 for s in sc) and sum(sc) / len(sc) > bs:
                best, bs = p, sum(sc) / len(sc)
    return best


def count(texts, prefix, per="auto"):
    """How many PREFIX tags the sheet labels, as an answer: dict(answer, unit="count", tags, gaps, explanation,
    evidence=[{id, shape: "box", bbox, label}], distinct, placements, stated_count, prefix, per). `prefix` may be a
    prefix ("CV", "PS-") or the words the sheet uses for it ("check valves", "hollow metal door").
    per="tag": distinct tags (CV-1..CV-5 -> 5); per="label": labels placed (PS-12 written at 7 supports counts 7);
    per="auto": labels when some number is written 3 or more times (the numbers then mark types), else tags."""
    items, L, B, defs, own, cands = _scan(texts)
    inv = _inventory(items, L, B, defs, own, cands)
    p = _resolve(inv, defs, prefix)
    if p is None:
        known = ", ".join(f"{k} ({v['count']})" for k, v in inv.items()) or "none"
        return dict(answer=None, unit="count", tags=[], gaps=[], prefix=None, per=per, distinct=0, placements=0,
                    stated_count=None, evidence=[],
                    explanation=f"No '{prefix}' tags were read in this sheet's text, and the sheet does not say which "
                                f"tag prefix means '{prefix}'. Tag prefixes read: {known}.")
    e = inv[p]
    if per == "auto":
        per = "label" if e["repeats"] and max(e["repeats"].values()) >= 3 else "tag"
    name = lambda n: f"{p}{e['form']}{n}"
    d = e["definition"]
    what = f" ({d['text'].lower()})" if d else ""
    listed = _ranges(e["numbers"], name) if len(e["tags"]) == len(e["numbers"]) else ", ".join(e["tags"])
    if per == "label":
        parts = [f"{e['placements']} {p} label{'s' if e['placements'] != 1 else ''}{what} placed on the sheet, with "
                 f"{e['count']} distinct number{'s' if e['count'] != 1 else ''}: {listed}."]
    else:
        parts = [f"{e['count']} distinct {p} tag{'s' if e['count'] != 1 else ''}{what} read on the sheet: {listed}."]
    if e["gaps"]:
        parts.append(e["gap_note"][0].upper() + e["gap_note"][1:] + ".")
    elif not e["sequence"]:
        parts.append("The numbers are not one sequence (model or type codes?), so no gaps are inferred.")
    if e["repeats"]:
        top = ", ".join(f"{t} x{n}" for t, n in list(e["repeats"].items())[:4])
        parts.append(f"Numbers are labelled more than once ({top}), so a {p} number marks a type rather than one item; "
                     + ("the answer counts labels (one label = one item)." if per == "label" else
                        f"{e['placements']} {p} labels are placed in all; the answer counts distinct tags."))
    legend = [o for o in e["occurrences"] if o["role"] == "legend"]
    if legend:
        parts.append(f"{len(legend)} of the labels are legend entries, not placements.")
    if e["unnumbered"]:
        n = len(e["unnumbered"])
        parts.append(f"{n} more text{'s' if n > 1 else ''} read just '{p}' with no number (a symbol, or a tag whose "
                     f"number was not read) {'are' if n > 1 else 'is'} not counted.")
    if e["mentions"]:
        parts.append(f"{len(e['mentions'])} mention{'s' if len(e['mentions']) > 1 else ''} inside notes not counted.")
    stated = next((x["stated_count"] for x in defs.get(p, []) if x.get("stated_count")), None)
    if d:
        st = next((x for x in defs.get(p, []) if x.get("stated_count")), d)
        parts.append(f"The sheet says: '{st['statement'][:160]}'.")
        if stated is not None:
            parts.append(f"It states {stated}: " + ("agrees." if stated == e["count"] else f"differs from the {e['count']} tags read."))
    else:
        parts.append(f"The sheet does not spell out what {p} stands for.")
    if e["corrections"]:
        parts.append("OCR slips corrected: " + "; ".join(e["corrections"][:4]) + ".")
    parts.append("Read from the sheet's text; a tag the text reader missed is not counted.")
    ev = [dict(id=f"{o['tag']}#{k}", shape="box", bbox=o["bbox"], label=o["tag"], text_ids=o["ids"])
          for k, o in enumerate(e["occurrences"])]
    if per == "label":
        ev = [x for x, o in zip(ev, e["occurrences"]) if o["role"] == "label"]
    return dict(answer=e["placements"] if per == "label" else e["count"], unit="count", tags=e["tags"], gaps=e["gaps"],
                prefix=p, per=per, distinct=e["count"], placements=e["placements"],
                stated_count=stated, definition=d["text"] if d else None, explanation=" ".join(parts), evidence=ev)


# ---------------------------------------------------------------- pipe sizes

INCH = re.compile(r"(?<![\d/.'′\w-])(\d{1,2}\s*-\s*\d{1,2}/\d{1,2}|\d{1,2}\s+\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}|\d{1,3}(?:\.\d+)?)"
                  r"\s*(?:\"|”|″|''|\bIN\b\.?|\bINCH(?:ES)?\b)")
METRIC = re.compile(r"\bDN\s?-?(\d{2,4})\b|[Øø⌀]\s?(\d{2,4})(?:\s?mm)?\b|%%[cC]\s?(\d{2,4})\b|\b(\d{2,4})\s?(?:MM|mm)\s?(?:Ø|DIA\b|DIAM)|"
                    r"\bNPS\s?(\d{1,2}(?:\s\d/\d)?)\b")
TAGS_ONLY = re.compile(r"(?:[A-Z]{1,5}[- ]?\d{1,4}[A-Z]?(?:\s*&\s*\d+)?[\s,]*|\d{1,2}(?:/\d{1,2})?\"\s*)+")
NOTPIPE = re.compile(r"^\s*(?:TALL|HIGH|WIDE|THICK|DEEP|LONG|O\.?C\.?|OC|CLR|CLEAR|MIN|MAX|GYP|GYPSUM|STUD|STUDS|LETTERS|"
                     r"CONC|CONCRETE|SLAB|BRICK|INSUL|INSULATION|BATT|RIGID|FURRING|PLY|PLYWOOD|BOARD|OSB|DEFLECTION|"
                     r"TOPPING|GROUT|GRAVEL|EMBED|CURB|WALL|FOAM|AIR\s+SPACE|AIRSPACE|BEARING|PER\s+FOOT|SPACING)\b")


def _inches(s):
    s = s.replace(" ", "")
    m = re.match(r"^(\d+)-?(\d+)/(\d+)$", s) if "/" in s and re.match(r"^\d+-", s) else None
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.match(r"^(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) / int(m.group(2)) if int(m.group(2)) else None
    return float(s)


def _sizes(B):
    out = []
    for blk in B:
        s = blk["text"]
        if re.search(r"\bSCALE\b", s, re.I) and "=" in s:
            continue
        found = []
        for m in INCH.finditer(s):
            pre = s[:m.start()]
            if re.search(r"['′]\s*-?\s*$", pre) or re.match(r"\s*=\s*\d+\s*['′]", s[m.end():]):
                continue  # feet-inches dimension or a scale "1/4" = 1'-0""
            raw = m.group(1)
            if re.match(r"^\d{1,2}\s+\d{1,2}/\d{1,2}$", raw) and re.search(r"\d\s*$", pre):
                continue
            try:
                val = _inches(raw)
            except ValueError:
                continue
            if not val or val > 120:
                continue
            found.append((m.start(), m.end(), raw.replace(" ", "") + '"', val, None))
        for m in METRIC.finditer(s):
            g = next(x for x in m.groups() if x)
            if m.group(5):
                found.append((m.start(), m.end(), "NPS " + g, _inches(g.replace(" ", "-")) if " " in g else float(g), None))
            else:
                found.append((m.start(), m.end(), ("DN" if m.group(1) else "Ø") + g, None, int(g)))
        found.sort()
        for k, (a, b, size, inch, mm) in enumerate(found):
            nxt = found[k + 1][0] if k + 1 < len(found) else len(s)
            after = re.split(r"[.;:()]|\s[-–]\s|\bSEE\b|\bX\b", s[b:nxt])[0]
            if k + 1 < len(found) and re.fullmatch(r"\s*[Xx×]\s*", s[b:nxt]):  # 10" X 12" REDUCER: both sizes, one fitting
                nn = found[k + 2][0] if k + 2 < len(found) else len(s)
                after = re.split(r"[.;:()]|\s[-–]\s|\bSEE\b|\bX\b", s[found[k + 1][1]:nn])[0]
            subject = " ".join(after.split()[:7]).strip(" ,&")
            if not subject:
                prev_end = found[k - 1][1] if k else 0
                subject = " ".join(re.split(r"[.;:()]", s[prev_end:a])[-1].split()[-5:]).strip(" ,&")
            if TAGS_ONLY.fullmatch(subject):
                subject = ""  # a size label stacked next to a tag label
            subject = re.sub(r"(?:\s+[A-Z]{1,5}-?\d{1,4}[A-Z]?)+$", "", subject)
            kind = "label" if not subject else "other" if NOTPIPE.match(subject) else "pipe"
            bb, ids, _ = _span(blk, a, b + len(s[b:b + len(after)].rstrip()))
            out.append(dict(size=size, inches=inch, mm=mm, subject=subject, kind=kind, text=s, bbox=bb, ids=ids))
    return out


def pipe_sizes(texts, about=None):
    """Every printed pipe size (2", 16", 1/2", 1-1/2", DN150, Ø150) with the text it occurs in and the words that follow
    it (`subject`: '16" SUCTION HEADER' -> 'SUCTION HEADER'). kind: 'pipe' (pipe words or a bare size label on a run),
    'other' (stud, board, letters, spacing ...), 'label' (a size alone). `about` keeps sizes whose text holds those
    words."""
    _, _, B = _layout(texts)
    out = _sizes(B)
    if about:
        q = _tokens(about if isinstance(about, str) else " ".join(about))
        out = [o for o in out if all(_word_score(w, _tokens(o["text"])) > 0 for w in q)]
    return out


def size_of(texts, words):
    """'what size is the suction header?' -> the size printed with those words, as an answer, or None."""
    q = [w for w in _tokens(words if isinstance(words, str) else " ".join(words))
         if w not in {"WHAT", "SIZE", "IS", "THE", "OF", "PIPE", "DIAMETER", "HOW", "BIG", "LARGE", "A"}] or _tokens(words)
    cands = []
    for o in pipe_sizes(texts):
        toks = _tokens(o["subject"])
        sc = [_word_score(w, toks) for w in q]
        if all(s > 0 for s in sc):
            cands.append((sum(sc) / len(sc) + (0.05 if o["kind"] == "pipe" else 0), o))
        else:  # the words are in the same text, not right after this size
            sc = [_word_score(w, _tokens(o["text"])) for w in q]
            if all(s > 0 for s in sc):
                cands.append((0.5 * sum(sc) / len(sc), o))
    if not cands:
        return None
    cands.sort(key=lambda c: -c[0])
    top = cands[0][1]
    same = [o for s, o in cands if o["size"] == top["size"] and s >= cands[0][0] - 0.1]
    other = sorted({o["size"] for s, o in cands if o["size"] != top["size"] and s >= cands[0][0] - 0.1})
    expl = f"Read '{top['size']} {top['subject']}' in the sheet's text ('{top['text'][:120]}')."
    if other:
        expl += f" Other sizes printed with the same words: {', '.join(other)}."
    unit = "in" if top["inches"] is not None else "mm"
    ans = top["inches"] if top["inches"] is not None else top["mm"]
    return dict(answer=ans, unit=unit, size=top["size"], explanation=expl, alternatives=other,
                evidence=[dict(id=f"size#{k}", shape="box", bbox=o["bbox"], label=o["size"], text_ids=o["ids"])
                          for k, o in enumerate(same)])


# ---------------------------------------------------------------- label = value

NUMBER = r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?"
VALUE = re.compile(rf"^\s*[=:]?\s*(?P<value>{NUMBER})\s*(?P<unit>(?:[A-Za-z²³%\"'][A-Za-z²³.\"'/]*)(?:\s[A-Za-z²³.]+)?)?\s*$")
INLINE = re.compile(rf"^(?P<label>[A-Za-z(][^=]{{1,80}}?)\s*[=:]\s*(?P<value>{NUMBER})\s*"
                    rf"(?P<unit>[A-Za-z²³%\"'][A-Za-z²³.\"'/]*(?:\s[A-Za-z²³.]+)?)?\s*$")


def _parse_num(s):
    s = s.replace(",", "")
    return int(s) if re.match(r"^[-+]?\d+$", s) else float(s)


def key_values(texts):
    """Printed 'label = value unit' pairs, in one line or as a label and a number on the same row in separate boxes:
    'WEST WING DORMITORY =' + '6,095 SF' -> dict(label='WEST WING DORMITORY', value=6095, unit='SF', raw, bbox, ids)."""
    _, L, _ = _layout(texts)  # blocks are built too: a short label may continue the line above
    out, used = [], set()
    for ln in L:
        m = INLINE.match(ln["text"])
        if m and not re.match(r"^\s*SCALE\b", m.group("label"), re.I) and sum(c.isalpha() for c in m.group("label")) >= 2:
            label = m.group("label").strip(" .:")
            blk = ln.get("block")
            k = blk["lines"].index(ln) if blk else 0
            if sum(c.isalpha() for c in label) <= 4 and k:  # 'EL. = 10000' under 'TOP OF SLAB'
                label = (blk["lines"][k - 1]["text"].split(". ")[-1] + " " + label).strip()
            out.append(dict(label=label, value=_parse_num(m.group("value")),
                            unit=(m.group("unit") or "").strip(), raw=ln["text"], bbox=[round(v, 1) for v in ln["bbox"]],
                            ids=ln["ids"]))
            used.add(id(ln))
    vals = [(ln, VALUE.match(ln["text"])) for ln in L if id(ln) not in used]
    vals = [(ln, m) for ln, m in vals if m]
    pairs = {}
    for v, m in vals:
        f, th = v["f"], v["th"]
        best, bg = None, None
        for lb in L:
            if lb is v or lb["vert"] != v["vert"] or id(lb) in used:
                continue
            g = lb["f"]
            mm = min(th, lb["th"])
            if min(f[3], g[3]) - max(f[1], g[1]) < 0.5 * mm:
                continue
            gap = f[0] - g[2]
            if gap < -0.2 * mm or gap > ROW_GAP * mm:
                continue
            if best is None or gap < bg:
                best, bg = lb, gap
        if best is None:
            continue
        lt = best["text"].strip()
        letters = sum(c.isalpha() for c in lt)
        eq = bool(re.search(r"[=:]\s*$", lt))
        unit = (m.group("unit") or "").strip()
        if letters < 3 or VALUE.match(lt) or not (eq or unit[:1].isalpha()):
            continue
        if re.match(r"^\s*SCALE\b", lt, re.I):
            continue
        if id(best) in pairs and pairs[id(best)][0] <= bg:
            continue
        pairs[id(best)] = (bg, best, v, m)
    for bg, lb, v, m in pairs.values():
        label = re.sub(r"\s*[=:]\s*$", "", lb["text"].strip())
        bb = [min(lb["bbox"][0], v["bbox"][0]), min(lb["bbox"][1], v["bbox"][1]),
              max(lb["bbox"][2], v["bbox"][2]), max(lb["bbox"][3], v["bbox"][3])]
        out.append(dict(label=label, value=_parse_num(m.group("value")), unit=(m.group("unit") or "").strip(),
                        raw=f"{label} = {v['text'].strip().lstrip('=: ')}", bbox=[round(x, 1) for x in bb],
                        ids=lb["ids"] + v["ids"]))
    out.sort(key=lambda d: (d["bbox"][1], d["bbox"][0]))
    return out
