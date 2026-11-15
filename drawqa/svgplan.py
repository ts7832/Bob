"""Plan drawings exported from a building model (SVG with one group per IFC element, as in the Junction dataset's
ifc_models/*_plan-*.svg, or drawn here from an .ifc by drawqa/ifcdraw.py): the elements are read exactly from the file
instead of being detected.

    read(svg_path, width_px, height_px) -> layer classes (picture pixel coordinates) or None when the SVG carries no
    IFC classes. Each item keeps the element's name and GUID; the source says they were read from the file.

An element drawn in several pieces (IfcOpenShell writes one group per piece, ids "...-product-<guid>-...") is one item.
"""
import re
import xml.etree.ElementTree as ET

MAP = {"IfcDoor": "door", "IfcWindow": "window", "IfcSpace": "room", "IfcWall": "wall", "IfcWallStandardCase": "wall",
       "IfcCurtainWall": "wall", "IfcStair": "stair", "IfcStairFlight": "stair", "IfcRamp": "ramp", "IfcColumn": "column",
       "IfcFurnishingElement": "furniture", "IfcFurniture": "furniture", "IfcRailing": "railing",
       "IfcSanitaryTerminal": "sanitary fixture", "IfcFlowTerminal": "fixture", "IfcBeam": "beam",
       "IfcTransportElement": "elevator"}
SOURCE = "read from the file (IFC classes in the SVG export)"
IFC_NS = "{http://www.ifcopenshell.org/ns}"
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


def _tag(e):
    return e.tag.rsplit("}", 1)[-1]


def read(svg_path, width_px, height_px):
    t = open(svg_path, encoding="utf-8", errors="replace").read()
    if 'class="Ifc' not in t:
        return None
    root = ET.fromstring(t.encode("utf-8"))
    vb = [float(x) for x in re.findall(_NUM, root.get("viewBox") or "")]
    if len(vb) != 4:
        return None
    vx, vy, vw, vh = vb
    kx, ky = width_px / vw, height_px / vh
    P = lambda p: [round((p[0] - vx) * kx, 1), round((p[1] - vy) * ky, 1)]
    elems = {}  # product key -> dict(cls, name, guid, subpaths)
    for g in root.iter():
        if _tag(g) != "g":
            continue
        cls = (g.get("class") or "").split()[0] if g.get("class") else ""
        name = MAP.get(cls)
        if not name:
            continue
        gid = g.get("id") or ""
        m = re.search(r"product-([0-9a-fA-F-]{36})", gid)
        guid = g.get("data-guid") or g.get(IFC_NS + "guid")
        key = (m.group(1) if m else None) or guid or gid or str(id(g))
        e = elems.setdefault(key, dict(cls=name, label=g.get("data-name") or g.get(IFC_NS + "name"), guid=guid, subs=[]))
        for p in g.iter():
            if _tag(p) == "path" and p.get("d"):
                e["subs"] += _subpaths(p.get("d"))
    classes = {}
    for e in elems.values():
        subs = e["subs"]
        pts = [p for s in subs for p in s]
        if len(pts) < 2:
            continue
        X, Y = [p[0] for p in pts], [p[1] for p in pts]
        it = dict(bbox=[*P((min(X), min(Y))), *P((max(X), max(Y)))])
        if e["label"]:
            it["label"] = e["label"]
        if e["guid"]:
            it["guid"] = e["guid"]
        if e["cls"] in ("room", "wall"):
            ring = max(subs, key=len)
            if len(ring) >= 3:
                it["polygon"] = [P(p) for p in ring]
                if e["cls"] == "wall":
                    from .segment import wall_axis
                    axis, thick = wall_axis(it["polygon"])
                    it.update(polyline=[axis[:2], axis[2:]], thickness_px=thick)
        c = classes.setdefault(e["cls"], dict(name=e["cls"], source=SOURCE, geometry="polygon" if "polygon" in it else "box", items=[]))
        c["items"].append(it)
    return list(classes.values()) or None
