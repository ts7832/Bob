"""Plan drawings exported from a building model (SVG with one group per IFC element, as in the Junction dataset's
ifc_models/*_plan-*.svg and similar exports): the elements are read exactly from the file instead of being detected.

    read(svg_path, width_px, height_px) -> layer classes (picture pixel coordinates) or None when the SVG carries no
    IFC classes. Each item keeps the element's name and GUID; the source says they were read from the file.
"""
import re

MAP = {"IfcDoor": "door", "IfcWindow": "window", "IfcSpace": "room", "IfcWall": "wall", "IfcWallStandardCase": "wall",
       "IfcCurtainWall": "wall", "IfcStair": "stair", "IfcStairFlight": "stair", "IfcRamp": "ramp", "IfcColumn": "column",
       "IfcFurnishingElement": "furniture", "IfcFurniture": "furniture", "IfcRailing": "railing",
       "IfcSanitaryTerminal": "sanitary fixture", "IfcFlowTerminal": "fixture", "IfcBeam": "beam"}
SOURCE = "read from the file (IFC classes in the SVG export)"
_NUM = r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?"


def _subpaths(d):
    """'M x,y L x,y ... A rx ry rot large sweep x y ...' -> list of point lists (one per M)."""
    out, cur = [], None
    for cmd, args in re.findall(r"([MLAHVZmlahvz])([^MLAHVZmlahvz]*)", d):
        v = [float(x) for x in re.findall(_NUM, args)]
        if cmd == "M":
            cur = []
            out.append(cur)
            cur += list(zip(v[0::2], v[1::2]))
        elif cmd == "L" and cur is not None:
            cur += list(zip(v[0::2], v[1::2]))
        elif cmd == "A" and cur is not None:
            for i in range(0, len(v) - 6, 7):
                cur.append((v[i + 5], v[i + 6]))
        elif cmd == "H" and cur:
            cur += [(x, cur[-1][1]) for x in v]
        elif cmd == "V" and cur:
            cur += [(cur[-1][0], y) for y in v]
    return [p for p in out if p]


def read(svg_path, width_px, height_px):
    t = open(svg_path, encoding="utf-8", errors="replace").read()
    vb = re.search(r'viewBox="\s*(' + _NUM + r')[\s,]+(' + _NUM + r')[\s,]+(' + _NUM + r')[\s,]+(' + _NUM + r')', t)
    if not vb or 'class="Ifc' not in t:
        return None
    vx, vy, vw, vh = map(float, vb.groups())
    kx, ky = width_px / vw, height_px / vh
    P = lambda p: [round((p[0] - vx) * kx, 1), round((p[1] - vy) * ky, 1)]
    classes = {}
    for cls, attrs, body in re.findall(r'<g[^>]*class="(Ifc\w+)"([^>]*)>(.*?)</g>', t, re.S):
        name = MAP.get(cls)
        if not name:
            continue
        subs = [s for d in re.findall(r'\sd="([^"]+)"', body) for s in _subpaths(d)]
        pts = [p for s in subs for p in s]
        if len(pts) < 2:
            continue
        X, Y = [p[0] for p in pts], [p[1] for p in pts]
        it = dict(bbox=[*P((min(X), min(Y))), *P((max(X), max(Y)))])
        m = re.search(r'data-name="([^"]*)"', attrs)
        if m and m.group(1):
            it["label"] = m.group(1)
        g = re.search(r'data-guid="([^"]*)"', attrs)
        if g:
            it["guid"] = g.group(1)
        if name in ("room", "wall"):
            ring = max(subs, key=len)
            if len(ring) >= 3:
                it["polygon"] = [P(p) for p in ring]
                if name == "wall":
                    from .segment import wall_axis
                    axis, thick = wall_axis(it["polygon"])
                    it.update(polyline=[axis[:2], axis[2:]], thickness_px=thick)
        c = classes.setdefault(name, dict(name=name, source=SOURCE, geometry="polygon" if "polygon" in it else "box", items=[]))
        c["items"].append(it)
    return list(classes.values()) or None
