"""Single entry point: solve(path, question) -> answer + evidence + explanation."""
import os
from functools import lru_cache

from . import router, query, swiss, scan

DATASET_SOURCES = ("swiss_dwellings", "cubicasa5k", "ifc_models", "ifc_bench")


def source_of(path):
    return next((s for s in DATASET_SOURCES if f"/{s}/" in path), "unknown")


@lru_cache(maxsize=64)
def load_sheet(path, generic=False):
    """generic=True: treat the file as unknown (no dataset shortcuts such as a DXF sibling or stored predictions)."""
    if generic:
        from . import convert
        return convert.convert(path)
    src = source_of(path)
    if src == "swiss_dwellings" and os.environ.get("DRAWQA_SWISS") == "pdf":  # read the PDF itself, no CAD file
        from . import convert
        return convert.convert(os.path.splitext(path)[0] + ".pdf")
    if src == "swiss_dwellings":
        dxf = os.path.splitext(path)[0] + ".dxf"  # same name = same drawing (README)
        sheet = swiss.load(dxf)
        sheet["render_source"] = dxf
        return sheet
    if src == "cubicasa5k" and os.environ.get("DRAWQA_LIVE") == "1":  # run the detector on the image now
        from . import convert
        return convert.convert(path)
    if src == "cubicasa5k":
        return scan.load(path)
    if src in ("ifc_models", "ifc_bench"):
        from . import ifc  # heavy import, only when needed
        return ifc.load(path)
    from . import convert  # any other file: recognise it by content
    return convert.convert(path)


SYMBOL_INTENTS = ("count_openings", "count_fixtures")


def honesty(sheet, intent, res):
    """How the answer was obtained, in the terms the judges asked for.

    tier: read (printed on the sheet) | measured (exact geometry or model data) | detected (trained detector found
    the elements; each one is shown) | estimated (depends on a stated assumption) | cannot determine
    """
    if res.get("answer") is None:
        return dict(tier="cannot determine", deterministic=None, basis=res.get("explanation", "not supported for this drawing"))
    st, it = sheet["source_type"], intent["intent"]
    scale = sheet.get("scale") or {}
    by_detector = st in ("raster", "vector-pdf") and (it in SYMBOL_INTENTS or (st == "raster" and it in ("count_rooms_class", "count_floors")))
    if by_detector:
        confs = [e.get("conf") for e in res.get("elements_used", []) if e.get("conf") is not None]
        low = sum(1 for c in confs if c < 0.6)
        note = f"{len(confs)} element(s) found by the trained detector; {low} with confidence below 0.6" if confs else "no element found by the trained detector"
        exact = sum(1 for e in res.get("elements_used", []) if e.get("exact"))
        if exact:
            note += f"; {exact} matched to rectangles in the drawing's own linework (exact geometry)"
        return dict(tier="detected", deterministic=False, basis=note, weakest=min(confs) if confs else None)
    if res.get("unit") in ("m2", "m", "m3") and st in ("vector-pdf", "raster"):
        src = " / ".join(sorted({r for r in res.get("area_sources", []) if r})) or "geometry"
        if scale.get("estimated") and "printed label" not in src:
            return dict(tier="estimated", deterministic=False, basis=f"{src}; scale {scale.get('source')} ({scale.get('length_error')})")
        if src == "printed label":
            return dict(tier="read", deterministic=True, basis="area printed on the sheet next to the room number")
        return dict(tier="measured", deterministic=True, basis=f"{src}; scale {scale.get('source', 'from the file')}")
    if st == "ifc":
        return dict(tier="measured", deterministic=True, basis="read from the building model's own elements and quantities")
    if st == "vector-dxf":
        src = " / ".join(sorted({r for r in res.get("area_sources", []) if r}))
        return dict(tier="read" if src == "printed label" else "measured", deterministic=True, basis=src or "exact CAD geometry")
    return dict(tier="read" if st == "vector-pdf" else "measured", deterministic=True, basis="text and linework of the file")


def solve(path, question, generic=False):
    sheet = load_sheet(path, generic)
    intent = router.parse(question)
    if sheet["source_type"] == "ifc":
        from . import ifc
        res = ifc.execute(sheet, intent)
    else:
        res = query.execute(sheet, intent)
    res.setdefault("evidence", [])
    res.setdefault("unit", None)
    res["intent"] = intent
    res["source_type"] = sheet["source_type"]
    res["method"] = {"vector-dxf": "exact vector geometry", "vector-pdf": "PDF text and linework", "ifc": "BIM model query",
                     "raster": sheet.get("model", "detector")}.get(sheet["source_type"], "n/a")
    res["honesty"] = honesty(sheet, intent, res)
    res.pop("elements_used", None)
    res.pop("area_sources", None)
    w = res["honesty"].get("weakest")
    res["confidence"] = (None if res.get("answer") is None else 0.99 if res["honesty"]["deterministic"]
                         else round(w, 2) if w is not None else 0.5)
    return res
