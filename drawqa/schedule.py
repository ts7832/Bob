"""Tables on a vector PDF sheet (door, window and finish schedules, room lists): read from the sheet's own text and
ruled lines. Nothing is detected or guessed: a table is a grid of drawn lines, a cell is the text inside one grid box.

    tables(path, page=0) -> [dict(title, columns=[name, ...], rows=[[cell, ...], ...], bbox, header_rows)]
    count_rows(table, column=None, value=None) / lookup(table, key, column)   deterministic queries over one table
    answer(tables, question) -> dict(answer, unit, explanation, table, evidence) or None when nothing matches

Why not PyMuPDF's find_tables(): on the real US drawing sets in the dataset (sheets rotated 90 degrees, thousands of
line items) it returns no table at all and takes 40-90 s. Here the page is turned upright first, then:
  1. ruled lines are collected and joined; lines that cross form a grid; each grid with enough lines is a table;
  2. the grid's long vertical lines are the column borders, its long horizontal lines the row borders;
  3. every word goes to the cell its centre falls in;
  4. the rows above the first run of evenly spaced rows are the header; their text names the columns.
Coordinates are upright page points (1/72 inch).
"""
import json, re

import pymupdf

TOL = 1.5  # points: lines closer than this are the same line; ends this close touch


def _upright(page):
    """Words and straight horizontal / vertical line segments in upright page coordinates."""
    M = page.rotation_matrix
    words = []
    for x0, y0, x1, y1, w, *_ in page.get_text("words"):
        r = pymupdf.Rect(x0, y0, x1, y1) * M
        r.normalize()
        words.append((r.x0, r.y0, r.x1, r.y1, w))
    H, V = [], []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                segs = [(it[1], it[2])]
            elif it[0] == "re":
                r = it[1]
                segs = [(r.tl, r.tr), (r.tr, r.br), (r.br, r.bl), (r.bl, r.tl)]
            elif it[0] == "qu":
                q = it[1]
                segs = [(q.ul, q.ur), (q.ur, q.lr), (q.lr, q.ll), (q.ll, q.ul)]
            else:
                continue
            for a, b in segs:
                a, b = a * M, b * M
                if abs(a.y - b.y) < 0.7 and abs(a.x - b.x) > 6:
                    H.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2))
                elif abs(a.x - b.x) < 0.7 and abs(a.y - b.y) > 6:
                    V.append((min(a.y, b.y), max(a.y, b.y), (a.x + b.x) / 2))
    return words, _join(H), _join(V)


def _join(segs):
    """Collinear segments that touch or overlap become one line: (start, end, position)."""
    lanes, last = [], None  # first group segments lying on the same line (positions within TOL of each other)
    for seg in sorted(segs, key=lambda s: s[2]):
        if last is None or seg[2] - last > TOL:
            lanes.append([])
        lanes[-1].append(seg)
        last = seg[2]
    out = []
    for lane in lanes:
        pos = sum(s[2] for s in lane) / len(lane)
        cur = None
        for lo, hi, _ in sorted(lane):
            if cur and lo <= cur[1] + TOL:
                cur[1] = max(cur[1], hi)
            else:
                if cur:
                    out.append((cur[0], cur[1], pos))
                cur = [lo, hi]
        out.append((cur[0], cur[1], pos))
    return out


def _grids(H, V):
    """Groups of lines that cross each other (union-find over horizontal x vertical crossings)."""
    n = len(H)
    parent = list(range(n + len(V)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    V_sorted = sorted(range(len(V)), key=lambda j: V[j][2])
    xs = [V[j][2] for j in V_sorted]
    import bisect
    for i, (x0, x1, y) in enumerate(H):
        for k in range(bisect.bisect_left(xs, x0 - TOL), bisect.bisect_right(xs, x1 + TOL)):
            j = V_sorted[k]
            if V[j][0] - TOL <= y <= V[j][1] + TOL:
                parent[find(i)] = find(n + j)
    groups = {}
    for i in range(n + len(V)):
        groups.setdefault(find(i), ([], []))[0 if i < n else 1].append(H[i] if i < n else V[i - n])
    return [g for g in groups.values() if len(g[0]) >= 4 and len(g[1]) >= 3]


def _distinct(vals):
    out = []
    for v in sorted(vals):
        if not out or v - out[-1] > TOL:
            out.append(v)
    return out


def _undouble(s):
    """A group heading over a column of the same word: "Frame Frame Type" -> "Frame Type"."""
    out = []
    for x in s.split():
        if not out or out[-1].lower() != x.lower():
            out.append(x)
    return " ".join(out)


def _text(ws):
    """Words of one cell in reading order (lines top to bottom, then left to right)."""
    ws = sorted(ws, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines = []
    for w in ws:
        if lines and abs((lines[-1][-1][1] + lines[-1][-1][3]) / 2 - (w[1] + w[3]) / 2) < 0.6 * (w[3] - w[1]):
            lines[-1].append(w)
        else:
            lines.append([w])
    return " ".join(" ".join(x[4] for x in sorted(l, key=lambda x: x[0])) for l in lines).strip()


def tables(path, page=0, min_rows=3):
    pg = pymupdf.open(path)[page]
    words, H, V = _upright(pg)
    out = []
    for hs, vs in _grids(H, V):
        x0, x1 = min(h[0] for h in hs), max(h[1] for h in hs)
        y0, y1 = min(v[0] for v in vs), max(v[1] for v in vs)
        if x1 - x0 < 60 or y1 - y0 < 30:
            continue
        cols = _distinct([v[2] for v in vs if v[1] - v[0] >= 0.5 * (y1 - y0)] + [x0, x1])
        rows = _distinct([h[2] for h in hs if h[1] - h[0] >= 0.5 * (x1 - x0)] + [y0, y1])
        if len(cols) < 3 or len(rows) < min_rows + 2:
            continue
        inside = [w for w in words if x0 - TOL <= (w[0] + w[2]) / 2 <= x1 + TOL and y0 - TOL <= (w[1] + w[3]) / 2 <= y1 + TOL]
        if len(inside) < 2 * (len(rows) - 1):
            continue  # a ruled frame with almost no text: a title block or a drawing border, not a table
        grid = [[[] for _ in range(len(cols) - 1)] for _ in range(len(rows) - 1)]
        import bisect
        for w in inside:
            ci = min(len(cols) - 2, max(0, bisect.bisect_right(cols, (w[0] + w[2]) / 2) - 1))
            ri = min(len(rows) - 2, max(0, bisect.bisect_right(rows, (w[1] + w[3]) / 2) - 1))
            grid[ri][ci].append(w)
        cells = [[_text(c) for c in r] for r in grid]
        heights = [rows[i + 1] - rows[i] for i in range(len(rows) - 1)]
        med = sorted(heights)[len(heights) // 2]
        # data = the rows of the common height; the header is what stands above the first of them
        first = next((i for i in range(len(heights)) if abs(heights[i] - med) <= 0.25 * med and any(cells[i])
                      and i + 1 < len(heights) and abs(heights[i + 1] - med) <= 0.25 * med), 0)
        # a header row with no column border running through it is one merged cell: the table's title, not column names
        crossed = lambda i: sum(1 for v in vs if x0 + TOL < v[2] < x1 - TOL and min(v[1], rows[i + 1]) - max(v[0], rows[i]) >= 0.6 * (rows[i + 1] - rows[i]))
        title_rows = [i for i in range(first) if crossed(i) == 0]
        cy = lambda w: (w[1] + w[3]) / 2
        hws = [w for w in inside if rows[0] <= cy(w) < rows[first] and not any(rows[i] <= cy(w) < rows[i + 1] for i in title_rows)]
        # each header word names every column under the cell it is printed in, and that cell is bounded by the column
        # borders running through the word's own height: a group heading ("Door", "Frame") sits above borders that start
        # lower down, so it spans the columns beneath it ("Door Matl", "Frame Matl"), even when the line under the group
        # row is too short to count as a row border
        hcol, tws = [[] for _ in range(len(cols) - 1)], []
        for w in hws:
            b = [x0] + [c for c in cols[1:-1] if any(abs(v[2] - c) <= TOL and v[0] - TOL <= cy(w) <= v[1] + TOL for v in vs)] + [x1]
            if len(b) == 2:  # no column border runs through this word: it spans the whole table, so it is the title
                tws.append(w)
                continue
            k = max(0, min(len(b) - 2, bisect.bisect_right(b, (w[0] + w[2]) / 2) - 1))
            for c in range(len(cols) - 1):
                if b[k] - TOL <= (cols[c] + cols[c + 1]) / 2 < b[k + 1] + TOL:
                    hcol[c].append(w)
        header = [[_undouble(_text(ws)) for ws in hcol]] if any(hcol) else []
        title = (" ".join(" ".join(c for c in cells[i] if c) for i in title_rows) + " " + _text(tws)).strip()
        above = sorted((w for w in words if x0 - TOL <= (w[0] + w[2]) / 2 <= x1 + TOL and y0 - 5 * med <= (w[1] + w[3]) / 2 < y0), key=lambda w: w[1])
        if not header and above:  # column names printed above the ruled grid instead of inside it
            band = [w for w in above if (w[1] + w[3]) / 2 >= y0 - 2.6 * med]
            header = [[_text([w for w in band if cols[c] <= (w[0] + w[2]) / 2 < cols[c + 1]]) for c in range(len(cols) - 1)]]
            above = [w for w in above if w not in band]
        names = [" ".join(dict.fromkeys(h[c] for h in header if h[c])).strip() or f"column {c + 1}" for c in range(len(cols) - 1)]
        data, spans = [], []
        for k in range(first, len(cells)):
            if any(cells[k]):
                data.append(cells[k])
                spans.append([round(rows[k], 1), round(rows[k + 1], 1)])
        if not data:  # short grids still go to the joining step (a group of one or two rows); see the end
            continue
        out.append(dict(title=title or _text(above), columns=names, rows=data, header_rows=first, page=page,
                        bbox=[round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)], row_spans=spans, row_h=med,
                        row_y=[round(v, 1) for v in rows[first:]], col_x=[round(v, 1) for v in cols]))
    out = [t for t in _join_continued(out) if len(t["rows"]) >= min_rows]
    return sorted(out, key=lambda t: -len(t["rows"]) * len(t["columns"]))


def _row_boxes(t, rows, px_per_pt, label=False):
    """Evidence: one box per matching row, over the row's own height (rows of a joined table come from several grids)."""
    ids = {id(r) for r in rows}
    spans = t.get("row_spans") or [[t["row_y"][i], t["row_y"][i + 1]] for i in range(len(t["row_y"]) - 1)]
    return [dict(id=f"row{i}", shape="box", label=r[0][:6] if label else "",
                 bbox=[t["bbox"][0] * px_per_pt, spans[i][0] * px_per_pt, t["bbox"][2] * px_per_pt, spans[i][1] * px_per_pt])
            for i, r in enumerate(t["rows"]) if id(r) in ids and i < len(spans)]


def _join_continued(out):
    """One schedule drawn as several grids, one per group (a Revit room schedule per level, with the group's subtotal
    printed between the grids): a grid with no header rows of its own, the same column borders as the grid just above
    it, and only a few row heights below it, continues that table. Its 'header' was guessed from the text above it,
    which is really the previous group's last rows and subtotal, so it is dropped."""
    same = lambda a, b: len(a) == len(b) and all(abs(p - q) <= 2 * TOL for p, q in zip(a, b))
    kept = []
    for t in sorted(out, key=lambda t: t["bbox"][1]):
        prev = next((k for k in reversed(kept) if same(k["col_x"], t["col_x"]) and t["header_rows"] == 0
                     and 0 <= t["bbox"][1] - k["bbox"][3] <= 4 * max(k["row_h"], t["row_h"])), None)
        if prev is None:
            kept.append(t)
            continue
        prev["rows"] += t["rows"]
        prev["row_spans"] += t["row_spans"]
        prev["bbox"][3] = t["bbox"][3]
        prev["parts"] = prev.get("parts", 1) + 1
    return kept


# ---- deterministic queries over one table -------------------------------------------------------------------------

def _norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def column_index(table, words):
    """The column whose header shares the most words with `words` (None when nothing matches)."""
    want = set(re.findall(r"[a-z0-9]+", _norm(words)))
    best = max(range(len(table["columns"])), key=lambda c: len(want & set(re.findall(r"[a-z0-9]+", _norm(table["columns"][c])))), default=None)
    return best if best is not None and want & set(re.findall(r"[a-z0-9]+", _norm(table["columns"][best]))) else None


def count_rows(table, column=None, value=None):
    """Rows with something in the first column; with column + value, only those whose cell equals the value."""
    rows = [r for r in table["rows"] if r[0]]
    if column is None:
        return rows
    return [r for r in rows if _norm(r[column]) == _norm(value)]


def lookup(table, key, column):
    r = next((r for r in table["rows"] if _norm(r[0]) == _norm(key)), None)
    return None if r is None else r[column]


# ---- questions over tables ----------------------------------------------------------------------------------------
# The question is matched against what the tables themselves contain (titles, column names, cell values), not against
# question templates: "door type A1" works because A1 is a value in a column called Door Type, in any language the
# sheet is written in.

COUNT_COLUMN = re.compile(r"^(count|qty|quantity|anzahl|stk|stück|nombre|quantité|quantità|määrä|kpl)\b", re.I)
_tok = lambda s: re.findall(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*", _norm(s))


def _pick_table(tabs, question):
    """The table whose title shares the most words with the question (ties: the larger table)."""
    q = set(_tok(question))
    score = lambda t: (len(q & set(_tok(t["title"]))), len(t["rows"]))
    best = max(tabs, key=score, default=None)
    return best if best and score(best)[0] > 0 else None


def guess(question, tabs):
    """Question -> dict(intent='schedule_lookup' | 'schedule_count', table=index, ...) or None."""
    if not tabs:
        return None
    q_tokens = _tok(question)
    q_set = set(q_tokens)
    is_count = re.search(r"how many|number of|wie viele|combien|quant[ie]|kuinka monta|hur många|hvor mange|cuánt", question, re.I) is not None
    # a row key named in the question (door 121.A, room 128): look in the table the question names first
    order = sorted(range(len(tabs)), key=lambda i: (-len(q_set & set(_tok(tabs[i]["title"]))), -len(tabs[i]["rows"])))
    if not is_count:
        for ti in order:
            t = tabs[ti]
            for kc in range(min(4, len(t["columns"]))):
                keys = {_norm(r[kc]): r for r in t["rows"] if r[kc]}
                hit = next((k for k in sorted(keys, key=len, reverse=True) if len(k) >= 2 and k in q_tokens and len([r for r in t["rows"] if _norm(r[kc]) == k]) == 1), None)
                if hit:
                    title_words = set(_tok(t["title"])) | set(_tok(t["columns"][kc]))
                    cols = sorted(range(len(t["columns"])), key=lambda c: -len((q_set - title_words) & set(_tok(t["columns"][c]))))
                    want = [c for c in cols if (q_set - title_words) & set(_tok(t["columns"][c])) and c != kc]
                    top = len((q_set - title_words) & set(_tok(t["columns"][want[0]]))) if want else 0
                    want = [c for c in want if len((q_set - title_words) & set(_tok(t["columns"][c]))) == top]
                    return dict(intent="schedule_lookup", table=ti, key_column=kc, key=hit, columns=sorted(want))
        return None
    t_idx = order[0] if q_set & set(_tok(tabs[order[0]]["title"])) else None
    sq = squash(question)
    # a dimension cell ("3' - 0\"") is never one word: compare without spaces, and not inside a longer number (13'-0")
    dim_in_q = lambda v: re.search(r"\d", v) and re.search(r"['\"]", v) and re.search(r"(?<![\d'])" + re.escape(squash(v)) + r"(?![\d])", sq)
    cands = []  # (table, column, value): a value from the question that is a cell value in a column
    for ti in (order if t_idx is None else [t_idx] + [i for i in order if i != t_idx]):
        t = tabs[ti]
        for c in range(len(t["columns"])):
            vals = {_norm(r[c]) for r in t["rows"] if r[c]}
            for v in vals:
                if 1 < len(v) <= 12 and v in q_tokens and not v.isalpha() or (v in q_tokens and len(v) >= 3 and question.find(v.upper()) >= 0) \
                        or dim_in_q(v):
                    named = len(q_set & set(_tok(t["columns"][c])))
                    cands.append((ti == t_idx, named, -ti, ti, c, v))
    if cands:
        _, _, _, ti, c, v = max(cands)
        return dict(intent="schedule_count", table=ti, column=c, value=v)
    return None  # no condition could be tied to a column: do not guess (a model-made plan can still answer, see plan())


def answer(tabs, intent, px_per_pt=150 / 72):
    """Run one table intent. Same shape as query.execute: answer, unit, evidence (boxes in page pixels), explanation."""
    if not tabs or intent is None or intent.get("table") is None or intent["table"] >= len(tabs):
        return dict(answer=None, explanation="No table on this sheet matches the question.", schedule_source=None)
    t = tabs[intent["table"]]
    name = t["title"] or "table"
    src = f"table '{name}' rebuilt from the PDF's own words and ruled lines ({len(t['rows'])} rows, {len(t['columns'])} columns)"

    def boxes(rows):
        return _row_boxes(t, rows, px_per_pt, label=True)

    if intent["intent"] == "schedule_lookup":
        r = next((r for r in t["rows"] if _norm(r[intent["key_column"]]) == _norm(intent["key"])), None)
        if r is None:
            return dict(answer=None, explanation=f"'{intent['key']}' is not a row of {name}.", schedule_source=src)
        cols = intent.get("columns") or []
        if not cols:
            return dict(answer=None, explanation=f"Row '{r[intent['key_column']]}' was found in {name}, but the question names none of its columns "
                                                 f"({', '.join(t['columns'])}).", schedule_source=src, evidence=boxes([r]))
        val = "; ".join(f"{t['columns'][c]}: {r[c]}" for c in cols) if len(cols) > 1 else r[cols[0]]
        # one area cell ("29690 SF") is a number with a unit, as in run(); anything else stays the cell's own text
        num = number(val) if len(cols) == 1 and re.search(r"\b(SF|m²|m2)\b", val, re.I) else None
        return dict(answer=num if num is not None else val, unit=("sqft" if re.search(r"SF", val, re.I) else "m2") if num is not None else None,
                    evidence=boxes([r]), schedule_source=src,
                    explanation=f"{name}, row '{r[intent['key_column']]}': " + "; ".join(f"{t['columns'][c]} = {r[c] or '(empty)'}" for c in cols) + ".")
    rows = count_rows(t, intent.get("column"), intent.get("value"))
    cc = next((c for c in range(len(t["columns"])) if COUNT_COLUMN.match(t["columns"][c])), None)
    if cc is not None and intent.get("column") is not None:  # a schedule by type lists a quantity per row: add those
        nums = [float(r[cc]) for r in rows if re.fullmatch(r"\d+(\.\d+)?", r[cc] or "")]
        if nums and len(nums) == len(rows):
            n = int(sum(nums)) if all(v.is_integer() for v in nums) else sum(nums)
            return dict(answer=n, unit="count", evidence=boxes(rows), schedule_source=src,
                        explanation=f"{name}: {len(rows)} row(s) with {t['columns'][intent['column']]} = '{intent['value']}'; their '{t['columns'][cc]}' cells add up to {n}.")
    what = f"with {t['columns'][intent['column']]} = '{intent['value']}'" if intent.get("column") is not None else "in total"
    return dict(answer=len(rows), unit="count", evidence=boxes(rows), schedule_source=src,
                explanation=f"{name}: {len(rows)} row(s) {what}; each one is highlighted.")


# ---- a language model turns the question into a query; code runs it -----------------------------------------------

def squash(v):
    """Cell text for comparison: case and spacing do not matter ("3' - 0\"" equals "3'-0\"")."""
    return re.sub(r"\s+", "", str(v)).lower()


def number(v):
    """Leading number of a cell ("29690 SF" -> 29690.0); None when there is none."""
    m = re.match(r"\s*(-?\d[\d,]*(?:\.\d+)?)", str(v))
    if not m:
        return None
    x = float(m.group(1).replace(",", ""))
    return int(x) if x.is_integer() else x


def prompt_for(question, tabs, samples=8):
    lines = ["Tables read from one construction drawing sheet:"]
    for i, t in enumerate(tabs):
        lines.append(f"\nTABLE {i}: \"{t['title']}\" ({len(t['rows'])} rows)")
        for c, name in enumerate(t["columns"]):
            vals = list(dict.fromkeys(r[c] for r in t["rows"] if r[c]))[:samples]
            more = len({r[c] for r in t["rows"] if r[c]}) - len(vals)
            lines.append(f"  column {c} \"{name}\": " + " | ".join(v[:28] for v in vals) + (f" | ... ({more} more values not shown)" if more > 0 else ""))
    lines.append(f"\nQuestion: {question}\n")
    lines.append(
        "Translate the question into ONE query over these tables. Do not answer it yourself. Reply with JSON only:\n"
        '{"table": <table number>, "op": "count" | "sum" | "lookup" | "max" | "min", "column": <column number or null>,\n'
        ' "where": [{"column": <column number>, "test": "equals" | "has_token" | "starts_with" | "not_empty", "values": ["..."]}]}\n'
        "count = number of rows matching every where-condition. sum = add the numbers in \"column\" over the matching rows "
        "(use it when the table lists a quantity or an area per row). lookup = the cell(s) of \"column\" in the matching "
        "rows; \"column\" may be a list of column numbers. max / min = the row with the largest / smallest number in "
        "\"column\". A condition is true when the cell equals one of the values (equals), contains one of them as a "
        "separate word or code (has_token), or starts with one (starts_with). Copy values exactly as they appear in the "
        "table. Only some example values are shown for each column; a door, room or type number named in the question "
        "is in the table even if it is not among the examples. Use as few conditions as the question needs: usually one. "
        "Words in brackets in the question explain a code and are not a second condition. For a kind of room or item "
        "(bathrooms, casement windows) use has_token with that one word, so that variants such as '1/2 BATH' or "
        "'Window-Casement-...' are included. If the answer needs arithmetic these operations cannot do (for example "
        "multiplying each row by a number taken from its name), or the question is about something drawn on the sheet "
        "and not listed in a table, reply {\"table\": null}.")
    return "\n".join(lines)


def parse_plan(text, tabs):
    """Model reply -> checked plan, or (None, reason). Anything that does not point at real tables and columns is refused."""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        p = json.loads(m.group(0)) if m else None
    except Exception:
        p = None
    if not isinstance(p, dict):
        return None, "the reply was not a query"
    if p.get("table") is None:
        return None, "the model found no table that answers the question"
    if not isinstance(p["table"], int) or not 0 <= p["table"] < len(tabs):
        return None, "the reply named a table that does not exist"
    n = len(tabs[p["table"]]["columns"])
    cols = p.get("column")
    cols = [] if cols is None else cols if isinstance(cols, list) else [cols]
    if p.get("op") not in ("count", "sum", "lookup", "max", "min") or not all(isinstance(c, int) and 0 <= c < n for c in cols):
        return None, "the reply used an unknown operation or column"
    if p["op"] in ("sum", "lookup", "max", "min") and not cols:
        return None, "the reply did not say which column to read"
    where = []
    for w in p.get("where") or []:
        if not (isinstance(w, dict) and isinstance(w.get("column"), int) and 0 <= w["column"] < n and w.get("test") in ("equals", "has_token", "starts_with", "not_empty")):
            return None, "the reply used an unknown condition"
        where.append(dict(column=w["column"], test=w["test"], values=[str(v) for v in (w.get("values") or [])]))
    return dict(table=p["table"], op=p["op"], columns=cols, where=where), None


def _match(cell, test, values):
    if test == "not_empty":
        return bool(str(cell).strip())
    if test == "equals":  # a long value may have been shortened in the prompt: its start is enough
        return any(squash(cell) == squash(v) or (len(squash(v)) >= 16 and squash(cell).startswith(squash(v).rstrip("-"))) for v in values)
    if test == "starts_with":
        return any(squash(cell).startswith(squash(v)) for v in values)
    sep = r"[\s,;/&+]+"
    toks = {squash(x) for x in re.split(sep, str(cell)) if x}
    # a plain word (letters only, 3+) may also be one part of a hyphenated or underscored name: "CASEMENT" in
    # "Window-Casement-Andersen-A_Series". Codes with digits ("AL-1", "U02") must be a whole token.
    parts = {p for x in toks for p in re.split(r"[-_.]+", x) if p}
    one = lambda v: squash(v) in toks or (str(v).strip().isalpha() and len(str(v).strip()) >= 3 and squash(v) in parts)
    # one word or code: a whole token of the cell; several ("MARVIN ESSENTIAL SLIDING", "AL-1/GL-1"): those, in that order
    return any(one(v) if len([x for x in re.split(sep, str(v)) if x]) < 2 else squash(v) in squash(cell)
               for v in values if str(v).strip())


def _matching(t, where):
    return [r for r in t["rows"] if all(_match(r[w["column"]] if w["column"] < len(r) else "", w["test"], w["values"]) for w in where)]


def _cond(t, w):
    return f"{t['columns'][w['column']]} {w['test'].replace('_', ' ')} {' / '.join(w['values'])}".strip()


def _empty_report(tabs, plan):
    """Why a query matched no row, in facts: rows per condition alone, and where a value missing here does occur."""
    t, out, missing = tabs[plan["table"]], [], []
    for w in plan["where"]:
        n = len(_matching(t, [w]))
        out.append(f"{_cond(t, w)}: {n} row(s) alone")
        if n == 0 and w["test"] != "not_empty":
            elsewhere = [f"table {j} '{u['title'][:40]}' column {c} '{u['columns'][c]}' ({k} rows)"
                         for j, u in enumerate(tabs) if j != plan["table"] for c in range(len(u["columns"]))
                         for k in [len(_matching(u, [dict(w, column=c)]))] if k]
            missing.append((w, elsewhere[:3]))
    return out, missing


def run(tabs, plan, px_per_pt=150 / 72):
    """Execute a checked plan. Deterministic; the explanation spells the query out so a person can redo it."""
    t = tabs[plan["table"]]
    name = t["title"] or "table"
    src = f"table '{name}' rebuilt from the PDF's own words and ruled lines ({len(t['rows'])} rows, {len(t['columns'])} columns)"
    rows = _matching(t, plan["where"])
    cond = " and ".join(_cond(t, w) for w in plan["where"]) or "all rows"

    def boxes(rs):
        return _row_boxes(t, rs, px_per_pt)

    base = dict(schedule_source=src, query=dict(table=name, where=cond, op=plan["op"], columns=[t["columns"][c] for c in plan["columns"]]))
    if not rows and plan["where"]:
        per, missing = _empty_report(tabs, plan)
        if missing:  # the query names something this table does not contain: no answer, rather than a confident 0
            w, elsewhere = missing[0]
            return dict(answer=None, **base, explanation=f"{name}: no row has {_cond(t, w)}, so this query cannot answer the question"
                        + (f" (that value does occur in {'; '.join(elsewhere)})" if elsewhere else "") + ". Rows per condition: " + "; ".join(per) + ".")
        if plan["op"] == "count":  # every condition matches some rows, just never together: a real 0, shown with its parts
            return dict(answer=0, unit="count", evidence=[], **base,
                        explanation=f"{name}: no row meets all of {cond}. Rows per condition alone: " + "; ".join(per) + ".")
    if plan["op"] == "count":
        qc = next((c for c in range(len(t["columns"])) if COUNT_COLUMN.match(t["columns"][c])), None)
        if qc is not None and rows and all(re.fullmatch(r"\d+", r[qc] or "") for r in rows):
            # a schedule by type lists how many of each: "how many" means those quantities, not the number of lines
            n = sum(int(r[qc]) for r in rows)
            return dict(answer=n, unit="count", evidence=boxes(rows), **base,
                        explanation=f"{name} lists a quantity per row: '{t['columns'][qc]}' added over the {len(rows)} row(s) where {cond} = {n}.")
        return dict(answer=len(rows), unit="count", evidence=boxes(rows), explanation=f"{name}: {len(rows)} row(s) where {cond}.", **base)
    c = plan["columns"][0]
    if plan["op"] == "sum":
        skipped = [r for r in rows if number(r[c]) is None]
        rows = [r for r in rows if number(r[c]) is not None]
        nums = [number(r[c]) for r in rows]
        if not rows:
            return dict(answer=None, explanation=f"{name}: no row where {cond} has a number in '{t['columns'][c]}'.", **base)
        if skipped:
            cond += f" ({len(skipped)} matching row(s) without a number in '{t['columns'][c]}' left out)"
        total = sum(nums)
        unit = next((u for u in ("SF", "m²", "m2") if any(u.lower() in str(r[c]).lower() for r in rows)), None)
        return dict(answer=int(total) if float(total).is_integer() else round(total, 2), unit={"SF": "sqft", "m²": "m2", "m2": "m2"}.get(unit, "count"),
                    evidence=boxes(rows), explanation=f"{name}: '{t['columns'][c]}' added over {len(rows)} row(s) where {cond} = {total:g}{' ' + unit if unit else ''}.", **base)
    if plan["op"] in ("max", "min"):
        scored = [(number(r[c]), r) for r in rows if number(r[c]) is not None]
        if not scored:
            return dict(answer=None, explanation=f"{name}: no number in '{t['columns'][c]}' among rows where {cond}.", **base)
        v, r = (max if plan["op"] == "max" else min)(scored, key=lambda x: x[0])
        return dict(answer=" ".join(x for x in r[:3] if x) + f" ({r[c]})", unit=None, evidence=boxes([r]),
                    explanation=f"{name}: the {'largest' if plan['op'] == 'max' else 'smallest'} '{t['columns'][c]}' among {len(scored)} row(s) is {r[c]}, in row {' / '.join(x for x in r[:3] if x)}.", **base)
    if not rows:
        return dict(answer=None, explanation=f"{name}: no row where {cond}.", **base)
    # several columns: name each one ("Width: 3' - 0\"; Height: 7' - 0\""); " x " between bare values read like dimensions
    many = len(plan["columns"]) > 1
    vals = list(dict.fromkeys("; ".join((f"{t['columns'][c2]}: " if many else "") + (r[c2] or "(empty)") for c2 in plan["columns"]) for r in rows))
    one = vals[0] if len(vals) == 1 else (" | " if many else "; ").join(vals)
    num = number(one) if len(vals) == 1 and len(plan["columns"]) == 1 and re.search(r"\b(SF|m²|m2)\b", one, re.I) else None
    return dict(answer=num if num is not None else one, unit=("sqft" if re.search(r"SF", one, re.I) else "m2") if num is not None else None, evidence=boxes(rows),
                explanation=f"{name}: {', '.join(t['columns'][c2] for c2 in plan['columns'])} in {len(rows)} row(s) where {cond}: {one}.", **base)


def plan(question, tabs):
    """Ask the model on Modal (tools/modal_reader.py) for a query. Returns (checked plan or None, note).
    A question that names exactly one row by its key and a column by its header needs no model: that is tried first."""
    g = guess(question, tabs)
    if g and g["intent"] == "schedule_lookup" and g.get("columns"):
        return dict(table=g["table"], op="lookup", columns=g["columns"], where=[dict(column=g["key_column"], test="equals", values=[g["key"]])]), \
            "row and column matched to the table's own values, no language model used"
    try:
        import modal
        ask = modal.Cls.from_name("drawqa-reader", "Reader")().text.remote
        r = ask(prompt_for(question, tabs), 500)
    except Exception as e:
        return None, f"language model not available ({type(e).__name__})"
    p, why = parse_plan(r["text"], tabs)
    note = why or f"query written by {r['model'].split('/')[-1]}, run by code"
    if p and p["where"] and not _matching(tabs[p["table"]], p["where"]):
        # it matched no row: show the model the facts once (rows per condition, where a missing value does occur) and
        # keep its second query only if that one matches rows. The answer is still computed by code from the cells.
        per, missing = _empty_report(tabs, p)
        t = tabs[p["table"]]
        facts = (f"\n\nA first query, on TABLE {p['table']} where " + " and ".join(_cond(t, w) for w in p["where"]) + ", matched NO row. "
                 "Rows matching each condition alone: " + "; ".join(per) + "."
                 + "".join(f" No row of TABLE {p['table']} has {_cond(t, w)}" + (f"; it occurs in {'; '.join(e)}." if e else ".") for w, e in missing)
                 + " Write the query again. Pick the table that lists the thing asked about, and keep only conditions the "
                   "question itself states (a description in brackets is not a condition).")
        try:
            r2 = ask(prompt_for(question, tabs) + facts, 500)
            p2, why2 = parse_plan(r2["text"], tabs)
        except Exception:
            p2 = None
        if p2 and _matching(tabs[p2["table"]], p2["where"]):
            # a condition of the first query that uses the question's own words ("3RD FLOOR" for "on the 3rd floor") and
            # that the second query dropped is put back, on whichever column of the new table contains it
            t2, carried = tabs[p2["table"]], []
            for w in p["where"]:
                if w["test"] == "not_empty" or not any(squash(v) and squash(v) in squash(question) for v in w["values"]):
                    continue
                if any({squash(v) for v in w["values"]} == {squash(v) for v in x["values"]} for x in p2["where"]):
                    continue
                for c in range(len(t2["columns"])):
                    w2 = dict(column=c, test="has_token", values=w["values"])
                    if _matching(t2, p2["where"] + [w2]):
                        p2["where"].append(w2)
                        carried.append(_cond(t2, w2))
                        break
            return p2, (f"query written by {r2['model'].split('/')[-1]} on its second try (the first matched no row), run by code"
                        + (f"; kept from the first try because the question says so: {', '.join(carried)}" if carried else ""))
    return p, note
