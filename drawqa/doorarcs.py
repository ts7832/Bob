"""Doors in a vector PDF, from their swing arcs: exact geometry, whatever the drawing style or language.

A door on a CAD plan is drawn as its leaf plus a quarter circle (the swing) centred on the hinge, with the door's
width as radius. In the PDF that arc is one or more cubic Bezier segments (CAD exporters split it into 45-degree or
90-degree pieces). Each segment's circle centre is where the normals at its two ends meet; segments that share a
centre and radius are one arc. An arc that sweeps about 90 degrees with a door-sized radius at the sheet's scale is a
door swing. Two swings whose hinges are one leaf apart and which close towards each other are one double door.

    doors(page, m_per_pt) -> dict(doors=[dict(center, radius_m, sweep_deg, bbox_pt, double)], arcs=..., notes)
"""
import math

MIN_R, MAX_R = 0.5, 1.4  # m: door leaves (a 1.4 m leaf is already a wide single door)
SWEEP = (70, 110)  # degrees: a door swing; folded or opened-90 conventions both draw about a quarter circle


def _center(p1, c1, c2, p2):
    """Centre of the circle a cubic Bezier approximates: where the normals at both ends meet. None if they are parallel."""
    t1 = (c1[0] - p1[0], c1[1] - p1[1])
    t2 = (p2[0] - c2[0], p2[1] - c2[1])
    n1 = (-t1[1], t1[0])
    n2 = (-t2[1], t2[0])
    det = n1[0] * (-n2[1]) - n1[1] * (-n2[0])
    if abs(det) < 1e-9:
        return None
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    s = (dx * (-n2[1]) - dy * (-n2[0])) / det
    return (p1[0] + s * n1[0], p1[1] + s * n1[1])


def _turn(p1, c1, c2, p2):
    t1 = (c1[0] - p1[0], c1[1] - p1[1])
    t2 = (p2[0] - c2[0], p2[1] - c2[1])
    a, b = math.hypot(*t1), math.hypot(*t2)
    if a < 1e-9 or b < 1e-9:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (t1[0] * t2[0] + t1[1] * t2[1]) / (a * b)))))


def arcs(page):
    """Circular arcs drawn on the page (in PDF points). A CAD exporter writes one arc as one path of 2-4 Bezier pieces
    (45 or 22.5 degrees each), so pieces are joined per path when they share a circle; the same arc drawn twice (some
    sheets repeat paths on two layers) is kept once."""
    out = []
    for d in page.get_drawings():
        segs = []
        for it in d["items"]:
            if it[0] != "c":
                continue
            p1, c1, c2, p2 = [(float(q.x), float(q.y)) for q in it[1:5]]
            c = _center(p1, c1, c2, p2)
            if c is None:
                continue
            r1, r2 = math.dist(c, p1), math.dist(c, p2)
            if r1 <= 0 or abs(r1 - r2) > 0.05 * max(r1, r2):  # not a circular piece (an ellipse, a spline)
                continue
            segs.append(dict(c=c, r=(r1 + r2) / 2, turn=_turn(p1, c1, c2, p2), ends=(p1, p2)))
        groups = []
        for s in segs:  # pieces of one path on the same circle (short pieces give a less exact centre: 10%)
            for g in groups:
                if math.dist(g["c"], s["c"]) < 0.1 * g["r"] and abs(g["r"] - s["r"]) < 0.05 * g["r"]:
                    g["sweep"] += s["turn"]
                    g["ends"] += s["ends"]
                    break
            else:
                groups.append(dict(c=s["c"], r=s["r"], sweep=s["turn"], ends=s["ends"]))
        groups += _polyline_arcs(d)  # exporters that flatten curves write an arc as a chain of short lines
        for g in groups:
            if not any(math.dist(g["c"], a["c"]) < 0.05 * g["r"] and abs(g["r"] - a["r"]) < 0.05 * g["r"]
                       and abs(g["sweep"] - a["sweep"]) < 10 for a in out):  # the same arc drawn twice
                out.append(g)
    return out


def _chain_arcs(page, max_seg):
    """Exporters that write every line as its own path (one US sheet: 56,604 one-line paths) leave an arc as short
    segments that only share end points. Chain segments end to end through points where exactly two meet, then test
    each chain of 6+ segments as an arc (circle fit)."""
    from collections import defaultdict
    key = lambda p: (round(p[0] * 20), round(p[1] * 20))  # 0.05 pt grid
    segs = []
    for d in page.get_drawings():
        if len(d["items"]) != 1 or d["items"][0][0] != "l":
            continue
        a, b = d["items"][0][1], d["items"][0][2]
        a, b = (float(a.x), float(a.y)), (float(b.x), float(b.y))
        if 1e-6 < math.dist(a, b) <= max_seg:
            segs.append((a, b))
    at = defaultdict(list)
    for i, (a, b) in enumerate(segs):
        at[key(a)].append(i)
        at[key(b)].append(i)
    used, out = set(), []
    for i in range(len(segs)):
        if i in used:
            continue
        chain, ends = [i], list(segs[i])
        used.add(i)
        for side in (0, 1):  # grow from both ends while the chain continues through a two-way point
            p = ends[side]
            while True:
                nxt = [j for j in at[key(p)] if j not in used]
                if len(at[key(p)]) != 2 or len(nxt) != 1:
                    break
                j = nxt[0]
                used.add(j)
                a, b = segs[j]
                p = b if key(a) == key(p) else a
                chain.insert(0, j) if side == 0 else chain.append(j)
            ends[side] = p
        if len(chain) < 6:
            continue
        pts = [ends[0]]  # walk the chain from one end to collect ordered points
        cur = ends[0]
        for j in chain:
            a, b = segs[j]
            nxtp = b if key(a) == key(cur) else a
            pts.append(nxtp)
            cur = nxtp
        f = _fit_circle(pts)
        if f is None:
            continue
        cx, cy, r, rms = f
        if rms > 0.02 * r:
            continue
        sweep = 0.0
        for p, q in zip(pts, pts[1:]):
            a1, a2 = math.atan2(p[1] - cy, p[0] - cx), math.atan2(q[1] - cy, q[0] - cx)
            sweep += (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        out.append(dict(c=(cx, cy), r=r, sweep=abs(math.degrees(sweep)), ends=(pts[0], pts[-1])))
    return out


def _fit_circle(pts):
    """Least-squares circle through points (Kasa fit) -> (cx, cy, r, rms residual) or None."""
    n = len(pts)
    sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts); syy = sum(p[1] * p[1] for p in pts); sxy = sum(p[0] * p[1] for p in pts)
    sz = [p[0] * p[0] + p[1] * p[1] for p in pts]
    sxz = sum(p[0] * z for p, z in zip(pts, sz)); syz = sum(p[1] * z for p, z in zip(pts, sz)); szs = sum(sz)
    # solve [sxx sxy sx; sxy syy sy; sx sy n] [a b c] = [sxz syz szs]
    M = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]]
    v = [sxz, syz, szs]
    det = lambda m: (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                     + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    D = det(M)
    if abs(D) < 1e-12:
        return None
    sol = []
    for i in range(3):
        Mi = [row[:] for row in M]
        for r in range(3):
            Mi[r][i] = v[r]
        sol.append(det(Mi) / D)
    a, b, c = sol
    cx, cy = a / 2, b / 2
    rr = c + cx * cx + cy * cy
    if rr <= 0:
        return None
    r = math.sqrt(rr)
    rms = math.sqrt(sum((math.dist((cx, cy), p) - r) ** 2 for p in pts) / n)
    return cx, cy, r, rms


def _polyline_arcs(d):
    """Arcs written as a chain of connected short lines (one path): fit a circle to its points; a tight fit over at
    least 6 points is an arc, whose sweep is the turned angle around the centre."""
    lines = [it for it in d["items"] if it[0] == "l"]
    if len(lines) < 5 or len(lines) != len(d["items"]):
        return []
    pts = [(float(lines[0][1].x), float(lines[0][1].y))]
    for it in lines:
        a, b = (float(it[1].x), float(it[1].y)), (float(it[2].x), float(it[2].y))
        if math.dist(a, pts[-1]) > 1e-3 * (1 + abs(a[0])):  # not one connected chain
            return []
        pts.append(b)
    f = _fit_circle(pts)
    if f is None:
        return []
    cx, cy, r, rms = f
    if rms > 0.02 * r or max(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) > 0.5 * r:
        return []
    sweep = 0.0
    for p, q in zip(pts, pts[1:]):
        a1, a2 = math.atan2(p[1] - cy, p[0] - cx), math.atan2(q[1] - cy, q[0] - cx)
        da = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        sweep += da
    return [dict(c=(cx, cy), r=r, sweep=abs(math.degrees(sweep)), ends=(pts[0], pts[-1]))]


def _in_legend(c, page):
    """Arcs inside a legend box (the sample door next to 'Porta' / 'Door') are symbols, not doors."""
    for b in page.search_for("Legend") + page.search_for("Legenda") + page.search_for("Legende") + page.search_for("LEGEND"):
        if b.x0 - 50 <= c[0] <= b.x0 + 600 and b.y0 - 20 <= c[1] <= b.y0 + 250:
            return True
    return False


def _tips(d):
    """The arc's end points away from the hinge line: where a closed leaf would lie, and where its tip swings to."""
    return d.get("ends", [])


def doors(page, m_per_pt):
    """Door swings among the page's arcs. m_per_pt: metres per PDF point at the sheet's scale."""
    found = []
    A = arcs(page)
    # arcs flattened into one-line paths: a door arc's pieces are well under a third of a leaf long
    for a in _chain_arcs(page, max_seg=0.35 * MAX_R / m_per_pt):
        if not any(math.dist(a["c"], b["c"]) < 0.05 * a["r"] and abs(a["r"] - b["r"]) < 0.05 * a["r"] for b in A):
            A.append(a)
    for a in A:
        r_m = a["r"] * m_per_pt
        if MIN_R <= r_m <= MAX_R and SWEEP[0] <= a["sweep"] <= SWEEP[1] and not _in_legend(a["c"], page):
            xs = [a["c"][0]] + [e[0] for e in a["ends"]]
            ys = [a["c"][1]] + [e[1] for e in a["ends"]]
            found.append(dict(center=a["c"], radius_m=round(r_m, 2), sweep_deg=round(a["sweep"]), ends=list(a["ends"]),
                              bbox_pt=[min(xs), min(ys), max(xs), max(ys)], double=False))
    # a double door: two swings whose hinges are about two leaves apart along one line, of similar radius -> one door
    used, out = set(), []
    for i, d in enumerate(found):
        if i in used:
            continue
        for j in range(i + 1, len(found)):
            e = found[j]
            if j in used or abs(d["radius_m"] - e["radius_m"]) > 0.25 * max(d["radius_m"], e["radius_m"]):
                continue
            gap = math.dist(d["center"], e["center"]) * m_per_pt
            # the two leaves meet in the middle: hinges one leaf + one leaf apart, AND the leaf tips touch
            tips = [p for p in _tips(d)] , [p for p in _tips(e)]
            meet = any(math.dist(p, q) * m_per_pt < 0.1 for p in tips[0] for q in tips[1])
            if meet and abs(gap - (d["radius_m"] + e["radius_m"])) < 0.15 * (d["radius_m"] + e["radius_m"]):
                used |= {i, j}
                b = [min(d["bbox_pt"][0], e["bbox_pt"][0]), min(d["bbox_pt"][1], e["bbox_pt"][1]),
                     max(d["bbox_pt"][2], e["bbox_pt"][2]), max(d["bbox_pt"][3], e["bbox_pt"][3])]
                out.append(dict(d, bbox_pt=b, double=True, radius_m=round(d["radius_m"] + e["radius_m"], 2)))
                break
        else:
            used.add(i)
            out.append(d)
    return dict(doors=out, swings=len(found))
