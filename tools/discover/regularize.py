"""Straighten nearly straight edges in polygons and polylines, conservatively.

    python regularize.py <in.json> <out.json> [--tol 5] [--min-len 12]

Works on any result file with classes/items (discovered.json, start_layer.json, model_final.json "rooms"). For each
polygon / polyline: simplify (1.5 px), find the drawing's main direction (length-weighted angle histogram over all items,
folded to 0-90 degrees), then snap every segment that is at least --min-len px long and within --tol degrees of the main
direction or its perpendicular onto an exact line at the segment's length-weighted position. Consecutive snapped
segments meet at the exact intersection of their lines. Short segments (curves, arcs) and real diagonals are left as
they are. Boxes are already straight and are not touched.
"""
import json, math, sys
import numpy as np
from shapely.geometry import LineString, Polygon


def segs(pts, closed):
    n = len(pts)
    return [(pts[i], pts[(i + 1) % n]) for i in range(n if closed else n - 1)]


def main_angle(all_pts):
    h = np.zeros(90)
    for pts, closed in all_pts:
        for (x0, y0), (x1, y1) in segs(pts, closed):
            L = math.hypot(x1 - x0, y1 - y0)
            if L < 1:
                continue
            a = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 90
            h[int(a) % 90] += L
    k = np.convolve(np.concatenate([h[-3:], h, h[:3]]), np.ones(5), "valid")[1:-1]
    a = float(np.argmax(k))
    return 0.0 if min(a, 90 - a) <= 2 else a  # a scan tilted by a degree or two is still drawn on the page axes


def simplify(pts, closed, tol=1.5):
    if closed:
        g = Polygon(pts).buffer(0)
        if g.geom_type != "Polygon":
            return pts
        g = g.simplify(tol, preserve_topology=True)
        return [list(p) for p in list(g.exterior.coords)[:-1]]
    return [list(p) for p in LineString(pts).simplify(tol).coords]


def regularize(pts, closed, theta, tol, min_len):
    pts = simplify(pts, closed)
    if len(pts) < (3 if closed else 2):
        return pts
    t = math.radians(theta)
    u = np.array([math.cos(t), math.sin(t)]); v = np.array([-math.sin(t), math.cos(t)])  # main axes
    lines = []  # per segment: None (keep) or (normal n, offset c) for the line n.p = c
    for (p0, p1) in segs(pts, closed):
        p0, p1 = np.array(p0, float), np.array(p1, float)
        d = p1 - p0; L = float(np.hypot(*d))
        if L < min_len:
            lines.append(None); continue
        ang = math.degrees(math.atan2(d[1], d[0]))
        for axis, normal in ((u, v), (v, u)):
            diff = abs((ang - math.degrees(math.atan2(axis[1], axis[0])) + 90) % 180 - 90)
            if diff <= tol:
                c = float(normal @ ((p0 + p1) / 2))
                lines.append((normal, c)); break
        else:
            lines.append(None)
    n = len(pts)
    out = []
    for i in range(n):
        prev = lines[i - 1] if (closed or i > 0) else None
        cur = lines[i] if (closed or i < n - 1) else None
        p = np.array(pts[i], float)
        if prev is not None and cur is not None and abs(float(prev[0] @ cur[0])) < 0.5:  # two perpendicular snapped lines
            A = np.array([prev[0], cur[0]]); b = np.array([prev[1], cur[1]])
            p = np.linalg.solve(A, b)
        else:
            for ln in (prev, cur):
                if ln is not None:  # move the vertex onto the snapped line
                    p = p - (float(ln[0] @ p) - ln[1]) * ln[0]
        out.append([round(float(p[0]), 1), round(float(p[1]), 1)])
    clean = [out[0]] + [q for k, q in enumerate(out[1:], 1) if math.hypot(q[0] - out[k - 1][0], q[1] - out[k - 1][1]) > 0.5]
    return clean


def run(d, tol=5.0, min_len=12.0):
    items = []
    for c in d.get("classes", []):
        items += [it for it in c.get("items") or []]
    items += d.get("rooms", [])  # plan_tool model format
    geo = [(it["polygon"], True) for it in items if it.get("polygon") and len(it["polygon"]) >= 3]
    geo += [(it["polyline"], False) for it in items if it.get("polyline") and len(it["polyline"]) >= 2]
    if not geo:
        return d, None
    theta = main_angle(geo)
    for it in items:
        if it.get("polygon") and len(it["polygon"]) >= 3:
            it["polygon"] = regularize(it["polygon"], True, theta, tol, min_len)
        if it.get("polyline") and len(it["polyline"]) >= 2:
            it["polyline"] = regularize(it["polyline"], False, theta, tol, min_len)
    return d, theta


if __name__ == "__main__":
    a = sys.argv
    tol = float(a[a.index("--tol") + 1]) if "--tol" in a else 5.0
    ml = float(a[a.index("--min-len") + 1]) if "--min-len" in a else 12.0
    d, th = run(json.load(open(a[1])), tol, ml)
    json.dump(d, open(a[2], "w"), indent=1)
    print(f"main direction {th} degrees; written {a[2]}")
