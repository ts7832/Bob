"""Floor plans from a building model (.ifc), one SVG per storey, drawn by IfcOpenShell (the same tool that made the
Junction dataset's plan exports). Each plan is then read like any plan export (drawqa/svgplan.py): every element
exactly, with its name and GUID.

    storeys(ifc_path, out_dir) -> [dict(name, guid, elevation, svg, counts)] sorted by elevation (cached in out_dir)

On a 12 MB model drawing all storeys takes about 20 s and 0.3 GB.

WORK IN PROGRESS, not used by the app yet: on the Esplanade model one storey matches the dataset's reference plan
exactly (42 doors, 60 spaces, 53 windows), but storey names come out shifted against the reference file names and the
upper storeys have doors only (no walls or spaces). Next: check the storey ids, then wire it into layer.page_source
(storey = page) with whole-building counts in a note.
"""
import json, os, re
import xml.etree.ElementTree as ET

IFC_NS = "http://www.ifcopenshell.org/ns"
ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
ET.register_namespace("ifc", IFC_NS)


def storeys(ifc_path, out_dir):
    idx = os.path.join(out_dir, "storeys.json")
    if os.path.exists(idx):
        return json.load(open(idx))
    import ifcopenshell, ifcopenshell.draw as draw
    f = ifcopenshell.open(ifc_path)
    elev = {s.GlobalId: float(s.Elevation or 0) for s in f.by_type("IfcBuildingStorey")}
    svg = draw.main(draw.draw_settings(auto_floorplan=True, space_names=True, door_arcs=True, include_projection=False), [f])
    svg = svg.decode("utf-8") if isinstance(svg, bytes) else svg
    root = ET.fromstring(svg.encode("utf-8"))
    tag = lambda e: e.tag.rsplit("}", 1)[-1]
    keep = [e for e in root if tag(e) in ("defs", "style")]
    # every element's group id names its storey: "storey-<storey id>-product-<element id>-<part>"; an element drawn in
    # several parts has several groups. (The storey groups themselves do not contain their elements.)
    heads = {}
    for g in root.iter():
        m = re.fullmatch(r"storey-([0-9a-fA-F-]{36})", g.get("id") or "")
        if tag(g) == "g" and m and g.get("class") == "IfcBuildingStorey":
            heads[m.group(1)] = g
    parent = {c: p for p in root.iter() for c in p}
    is_prod = lambda e: re.match(r"storey-[0-9a-fA-F-]{36}-product-", e.get("id") or "")
    groups, products = {}, {}
    for g in root.iter():
        m = re.match(r"storey-([0-9a-fA-F-]{36})-product-([0-9a-fA-F-]{36})", g.get("id") or "")
        if tag(g) != "g" or not m:
            continue
        a = parent.get(g)
        while a is not None and not is_prod(a):
            a = parent.get(a)
        if a is not None:  # part of an element group already taken
            continue
        groups.setdefault(m.group(1), []).append(g)
        if (g.get("class") or "").startswith("Ifc"):
            products.setdefault(m.group(1), {}).setdefault(m.group(2), g.get("class").split()[0])
    out = []
    os.makedirs(out_dir, exist_ok=True)
    for i, (sid, gs) in enumerate(groups.items()):
        h = heads.get(sid)
        name = (h.get(f"{{{IFC_NS}}}name") if h is not None else None) or f"storey {i + 1}"
        guid = h.get(f"{{{IFC_NS}}}guid") if h is not None else None
        doc = ET.Element(root.tag, dict(root.attrib))
        for e in keep:
            doc.append(e)
        for g in gs:
            doc.append(g)
        path = os.path.join(out_dir, f"storey_{i:02d}.svg")
        ET.ElementTree(doc).write(path, encoding="utf-8", xml_declaration=True)
        counts = {}
        for c in products.get(sid, {}).values():
            counts[c] = counts.get(c, 0) + 1
        out.append(dict(name=name, guid=guid, elevation=elev.get(guid, i), svg=path, counts=counts))
    out.sort(key=lambda s: s["elevation"])
    json.dump(out, open(idx, "w"), indent=1)
    return out


def default_storey(st):
    """The storey a person would open first: the one with the most rooms, the lowest of those at or above ground."""
    if not st:
        return 0
    rooms = lambda s: s["counts"].get("IfcSpace", 0) + s["counts"].get("IfcDoor", 0) / 10
    best = max(rooms(s) for s in st)
    good = [i for i, s in enumerate(st) if rooms(s) >= 0.8 * best]
    above = [i for i in good if st[i]["elevation"] >= 0]
    return (above or good)[0]
