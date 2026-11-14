"""Vector PDF floor plans -> sheet graph, without any CAD file.

What comes from where:
  texts, room names/numbers/printed areas, scale  <- the PDF text layer (exact)
  apartment (unit) boundaries                     <- dashed outlines in the PDF linework (exact)
  room outline area when no area is printed       <- region around the room label on the rendered page (measured)
  doors, windows, fixtures                        <- the detector on the rendered page (plugged in by convert.py)
Coordinates are page pixels at DPI (y down), so everything overlays on the rendered image.
"""
import re

import numpy as np
import pymupdf

from .geom import point_in_polygon, shoelace
from .swiss import ROOM_NO, AREA_TXT, UNIT_LABEL, OUTDOOR

DPI = 150
SCALE_TXT = re.compile(r"(?:scala|ma(?:ss|ß)stab|[ée]chelle|scale|mittakaava|m)\s*[:=]?\s*1\s*[:/]\s*(\d{1,4})\b", re.I)


def render(page, dpi=DPI):
    pm = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
    return np.frombuffer(pm.samples, np.uint8).reshape(pm.h, pm.w, pm.n)[:, :, :3][:, :, ::-1].copy()  # BGR


def spans(page, k):
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            for s in l["spans"]:
                t = s["text"].strip()
                if t:
                    out.append(dict(text=t, bbox=[v * k for v in s["bbox"]], bold="Bold" in s["font"]))
    return out


IMPERIAL = re.compile(r"""(?:(\d+)\s+)?(\d+)\s*/\s*(\d+)\s*(?:"|in)\s*=\s*1\s*'\s*-?\s*0?\s*"?|(\d+)\s*(?:"|in)\s*=\s*(\d+)\s*'""", re.I)


def scale_denominator(text):
    """'Scala 1:100' -> 100 ; '1/8" = 1'-0"' -> 96 ; '1" = 20'' -> 240. None if the text is not a scale."""
    m = SCALE_TXT.search(text)
    if m:
        return int(m.group(1))
    m = IMPERIAL.search(text)
    if m:
        if m.group(2):  # fraction of an inch per foot
            inch = (int(m.group(1) or 0)) + int(m.group(2)) / int(m.group(3))
            return round(12 / inch, 2) if inch else None
        return round(int(m.group(5)) * 12 / int(m.group(4)), 2)
    return None


def find_scale(texts, dpi=DPI):
    """Printed drawing scale -> metres per pixel of the rendered page.

    A sheet can carry several views at different scales; then no single scale is chosen and every candidate is
    listed with its position, so a measurement can use the one printed under its own view."""
    cands = []
    for t in texts:
        den = scale_denominator(t["text"])
        if den and 1 <= den <= 5000:
            cands.append(dict(denominator=den, text=t["text"], text_id=t["id"], bbox=t["bbox"]))
    distinct = sorted({c["denominator"] for c in cands})
    if len(distinct) == 1:
        c = cands[0]
        return dict(m_per_px=c["denominator"] * 0.0254 / dpi, denominator=c["denominator"], source=f"printed '{c['text']}'",
                    text_id=c["text_id"], candidates=cands)
    if distinct:
        return dict(m_per_px=None, denominator=None, candidates=cands,
                    source=f"several scales printed on this sheet: {', '.join('1:' + str(d).rstrip('0').rstrip('.') for d in distinct)}")
    return dict(m_per_px=None, denominator=None, source="no printed scale found", candidates=[])


def dashed_outlines(page, k):
    """Closed dashed polylines (apartment boundaries in the Swiss sheets)."""
    out = []
    for d in page.get_drawings():
        if d.get("dashes") in (None, "[] 0") or len(d["items"]) < 3:
            continue
        pts = []
        for it in d["items"]:
            if it[0] == "l":
                for p in (it[1], it[2]):
                    q = (p.x * k, p.y * k)
                    if not pts or abs(pts[-1][0] - q[0]) + abs(pts[-1][1] - q[1]) > 0.5:
                        pts.append(q)
        if len(pts) >= 3:
            out.append(pts)
    return out


def filled_polygons(page, k):
    """Closed filled shapes made of straight segments (rooms are drawn this way; so are some walls and fixtures)."""
    out = []
    for d in page.get_drawings():
        if "f" not in (d.get("type") or "") or d.get("dashes") not in (None, "[] 0"):
            continue
        pts = []
        for it in d["items"]:
            if it[0] == "l":
                for p in (it[1], it[2]):
                    q = (p.x * k, p.y * k)
                    if not pts or abs(pts[-1][0] - q[0]) + abs(pts[-1][1] - q[1]) > 0.3:
                        pts.append(q)
            elif it[0] == "re":
                r = it[1]
                pts += [(r.x0 * k, r.y0 * k), (r.x1 * k, r.y0 * k), (r.x1 * k, r.y1 * k), (r.x0 * k, r.y1 * k)]
            else:
                pts = []
                break
        if len(pts) >= 3:
            if abs(pts[0][0] - pts[-1][0]) + abs(pts[0][1] - pts[-1][1]) < 0.3:
                pts = pts[:-1]
            style = (tuple(round(c, 2) for c in (d.get("color") or ())), round(d.get("width") or 0, 2))
            out.append((pts, style))
    return out


def quads(page, k):
    """Every path in the linework that is one closed four-sided shape -> its bounding box in page pixels.
    Doors and windows are drawn as such rectangles; used to snap detected openings to the exact linework."""
    out = []
    for d in page.get_drawings():
        if d.get("dashes") not in (None, "[] 0"):
            continue
        it = d["items"]
        pts = None
        if len(it) == 1 and it[0][0] == "qu":
            q = it[0][1]
            pts = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
        elif len(it) == 1 and it[0][0] == "re":
            r = it[0][1]
            pts = [(r.x0, r.y0), (r.x1, r.y1)]
        elif 3 <= len(it) <= 5 and all(i[0] == "l" for i in it):
            p = [(i[1].x, i[1].y) for i in it] + [(it[-1][2].x, it[-1][2].y)]
            closed = abs(p[0][0] - p[-1][0]) + abs(p[0][1] - p[-1][1]) < 0.3 or d.get("closePath")
            if closed and len({(round(x, 1), round(y, 1)) for x, y in p}) == 4:
                pts = p
        if pts:
            xs, ys = [x * k for x, _ in pts], [y * k for _, y in pts]
            if max(xs) - min(xs) >= 2 and max(ys) - min(ys) >= 2:
                style = (tuple(round(c, 2) for c in (d.get("color") or ())), round(d.get("width") or 0, 2), "f" in (d.get("type") or ""))
                out.append(([min(xs), min(ys), max(xs), max(ys)], style))
    return out


def load(path, page_no=0, dpi=DPI):
    page = pymupdf.open(path)[page_no]
    k = dpi / 72
    T = spans(page, k)
    for i, t in enumerate(T):
        t["id"] = f"t{i}"
    scale = find_scale(T, dpi)
    centre = lambda t: ((t["bbox"][0] + t["bbox"][2]) / 2, (t["bbox"][1] + t["bbox"][3]) / 2)
    rooms, unit_labels = [], []
    for i, t in enumerate(T):
        s = t["text"]
        m = UNIT_LABEL.match(s)
        if m:
            unit_labels.append((m.group(2), *centre(t)))
            continue
        if not ROOM_NO.match(s) or not ("." in s or t["bold"]):
            continue  # bare digits in plain type are scale-bar ticks and dimensions, not room numbers
        prev = T[i - 1]["text"] if i > 0 else ""
        name = prev if prev and not re.search(r"\d", prev) and not UNIT_LABEL.match(prev) else None
        nxt = T[i + 1]["text"] if i + 1 < len(T) else ""
        m2 = AREA_TXT.match(nxt)
        area = float(m2.group(1)) if m2 else None
        x, y = centre(t)
        ids = [T[j]["id"] for j in ((i - 1) if name else i, i, (i + 1) if m2 else i)]
        rooms.append(dict(id=f"room{i}", number=s, name=name, text_pos=[x, y], text_ids=sorted(set(ids)),
                          bbox=[min(T[j]["bbox"][0] for j in (i - 1 if name else i, i)), T[i - 1 if name else i]["bbox"][1],
                                max(T[j]["bbox"][2] for j in (i - 1 if name else i, i)), T[i + 1 if m2 else i]["bbox"][3]],
                          area_m2=area, label_area=area, geom_area=None,
                          area_source="printed label" if area is not None else None,
                          outdoor=bool(name and OUTDOOR.match(name))))
    # room outline = the smallest filled polygon that contains the room number (same rule as the CAD reader);
    # its area is exact geometry, converted to m2 with the printed scale
    polys = [(abs(shoelace(P)), P, st) for P, st in filled_polygons(page, k)]
    mpp = scale["m_per_px"]
    big = lambda a: mpp is None or a * mpp * mpp >= 0.3

    def smallest(x, y, style=None):
        c = [(a, P, st) for a, P, st in polys if big(a) and (style is None or st == style) and point_in_polygon(x, y, P)]
        return min(c, key=lambda v: v[0]) if c else None

    # which drawing style are room outlines on this sheet? -> the style most room labels sit in (learned per sheet)
    votes = {}
    for r in rooms:
        hit = smallest(*r["text_pos"])
        if hit:
            votes[hit[2]] = votes.get(hit[2], 0) + 1
    room_style = max(votes, key=votes.get) if votes else None
    for r in rooms:
        hit = smallest(*r["text_pos"], style=room_style)
        if hit:
            a, P, _ = hit
            r["polygon"] = P
            if mpp is not None:
                r["geom_area"] = round(a * mpp * mpp, 2)
                if r["area_m2"] is None:
                    r["area_m2"], r["area_source"] = r["geom_area"], f"outline geometry at scale 1:{scale['denominator']}"
    units = []
    for j, P in enumerate(dashed_outlines(page, k)):
        lab = next((L for L, x, y in unit_labels if point_in_polygon(x, y, P)), None)
        units.append(dict(id=f"unit{j}", label=lab, polygon=P, residential=lab is not None))
    for r in rooms:
        x, y = r["text_pos"]
        r["unit"] = next((u["label"] for u in units if u["label"] and point_in_polygon(x, y, u["polygon"])), None)
    return dict(source=path, source_type="vector-pdf", units="px", y_flip=False, page=page_no, dpi=dpi,
                width_px=round(page.rect.width * k), height_px=round(page.rect.height * k), scale=scale,
                texts=T, rooms=rooms, units_list=units, openings=[], fixtures=[], floors=[],
                render=dict(type="pdf", width=round(page.rect.width * k), height=round(page.rect.height * k)))
