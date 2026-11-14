"""IFC models (ifc_models): answers straight from the BIM model; evidence = element GlobalIds.

Validated on the preview set: 513/525 English questions exact (misses = model not in preview).
"""
import os, re, glob
import numpy as np
import ifcopenshell, ifcopenshell.geom
import ifcopenshell.util.element as UE
import ifcopenshell.util.unit as UU

CLS = {"door": "IfcDoor", "window": "IfcWindow", "wall": "IfcWall", "column": "IfcColumn", "beam": "IfcBeam",
       "stair": "IfcStair", "space": "IfcSpace", "slab": "IfcSlab", "sanitary": "IfcFlowTerminal"}
_models = {}
_GS = ifcopenshell.geom.settings()
_GS.set("use-world-coords", True)


def model_path(path):
    stem = re.sub(r"_plan-.*$", "", os.path.splitext(os.path.basename(path))[0])
    f = os.path.join(os.path.dirname(path), stem + ".ifc")
    return f if os.path.exists(f) else None


def plan_svg(path):
    if path.endswith(".svg"):
        return path
    svg = os.path.splitext(path)[0] + ".svg"
    if os.path.exists(svg):
        return svg
    stem = os.path.splitext(path)[0]
    found = sorted(glob.glob(stem + "_plan-*.svg"))
    return found[0] if found else None


def _model(f):
    if f not in _models:
        _models[f] = ifcopenshell.open(f)
    return _models[f]


def _qty(el, *names):
    for pset in UE.get_psets(el, qtos_only=True).values():
        for n in names:
            if isinstance(pset.get(n), (int, float)):
                return float(pset[n])
    return None


def _footprint(el):
    try:
        sh = ifcopenshell.geom.create_shape(_GS, el)
    except Exception:
        return None
    v = np.array(sh.geometry.verts).reshape(-1, 3)
    f = np.array(sh.geometry.faces).reshape(-1, 3)
    n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    return float(-n[n[:, 2] < -1e-9, 2].sum() / 2)


def _area(sp):
    q = _qty(sp, "NetFloorArea", "GrossFloorArea", "NetArea")
    return (q, "IFC quantity set") if q else (_footprint(sp), "3D geometry footprint")


def _storey(el):
    c = UE.get_container(el)
    while c is not None and not c.is_a("IfcBuildingStorey"):
        c = UE.get_aggregate(c) if c.is_a("IfcSpace") else UE.get_container(c)
    return c


def _elements(m, cls, storey=None):
    els = m.by_type(CLS[cls])
    if cls == "space":
        return [e for e in els if storey is None or (UE.get_aggregate(e) and UE.get_aggregate(e).Name == storey)]
    if cls == "stair":
        els = [e for e in els if UE.get_aggregate(e) is None]  # skip stair sub-parts
    return [e for e in els if storey is None or ((s := _storey(e)) is not None and s.Name == storey)]


def load(path):
    svg = plan_svg(path)
    return dict(source=path, source_type="ifc", model=model_path(path),
                render=dict(type="svg", svg_path=svg) if svg else dict(type="none"))


def execute(sheet, intent):
    f = sheet.get("model")
    if f is None:
        return dict(answer=None, explanation="The BIM model for this plan is not available; this needs the model or the other floor plans.")
    m = _model(f)
    it = intent["intent"]
    ev = lambda els, lab=None: [dict(id=e.GlobalId, shape="guid", label=lab(e) if lab else None) for e in els]
    if it == "ifc_count" or (it in ("count_openings", "count_fixtures") and intent.get("cls") in CLS):
        els = _elements(m, intent["cls"], intent.get("storey"))
        where = f"storey '{intent['storey']}'" if intent.get("storey") else "the whole model"
        return dict(answer=len(els), unit="count", evidence=ev(els, None),
                    explanation=f"{len(els)} {CLS[intent['cls']]} elements in {where}.")
    if it == "ifc_room_area":
        sp = [s for s in _elements(m, "space", intent["storey"]) if s.Name == intent["number"]]
        if not sp:
            return dict(answer=None, explanation="Space not found.")
        a, src = _area(sp[0])
        return dict(answer=round(a, 2), unit="m2", evidence=ev(sp, lambda e: f"{e.Name} {a:.2f} m²"),
                    explanation=f"IfcSpace {sp[0].Name} '{sp[0].LongName}': {a:.2f} m² from {src}.")
    if it == "ifc_storey_area":
        sp = _elements(m, "space", intent["storey"])
        areas = [(s, _area(s)[0] or 0) for s in sp]
        tot = round(sum(a for _, a in areas), 2)
        return dict(answer=tot, unit="m2", evidence=ev(sp), explanation=f"Sum of {len(sp)} spaces on '{intent['storey']}' = {tot:.2f} m².")
    if it == "ifc_quantity":
        names = {"net volume": ("NetVolume", "GrossVolume"), "length": ("Length",),
                 "net side area": ("NetSideArea", "GrossSideArea")}[intent["quantity"]]
        els = _elements(m, intent["cls"], intent["storey"])
        tot = sum(_qty(e, *names) or 0 for e in els)
        if intent["quantity"] == "length":
            tot *= UU.calculate_unit_scale(m)
        unit = {"net volume": "m3", "length": "m", "net side area": "m2"}[intent["quantity"]]
        return dict(answer=round(tot, 3), unit=unit, evidence=ev(els),
                    explanation=f"Sum of {intent['quantity']} over {len(els)} {intent['cls']}s on '{intent['storey']}' = {tot:.3f} {unit}.")
    return dict(answer=None, explanation=f"Not supported for BIM yet ({it}).")
