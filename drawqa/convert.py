"""Any drawing file -> one unified sheet (the single format every question is answered from).

    sheet = convert(path)            # PDF, PNG, JPG, DXF or IFC, recognised by content, not by name or folder
    save(sheet, "out.json")

Unified sheet (format "drawqa-sheet/1"), all coordinates in pixels of the page image, y down:
    source, kind, page, width_px, height_px
    scale     {m_per_px, denominator, source}            how lengths/areas become metres, and where that came from
    elements  [{id, type, bbox, conf, found_by}]          doors, windows, fixtures (detector or exact geometry)
    texts     [{id, text, bbox}]                          everything printed on the sheet
    rooms     [{id, name, number, type, bbox|polygon, area_m2, area_source}]
    floors    [{id, bbox}]                                separate storey drawings on one sheet
`openings` / `fixtures` are views of `elements` kept for the question engine.
"""
import json, os

from . import detect

OPENINGS = {"door", "window"}
ROOM_TYPES = {"bedroom", "kitchen", "bathroom", "wc_room", "balcony", "sauna_room"}


def sniff(path):
    """File kind from its first bytes."""
    with open(path, "rb") as f:
        head = f.read(2048)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or head[:2] == b"\xff\xd8" or head[:4] in (b"II*\x00", b"MM\x00*") or head[:2] == b"BM":
        return "image"
    if b"ISO-10303-21" in head[:64]:
        return "ifc"
    if head[:6] in (b"AC1012", b"AC1014", b"AC1015", b"AC1018", b"AC1021", b"AC1024", b"AC1027", b"AC1032"):
        return "dwg"
    text = head.decode("latin1", "replace")
    if "SECTION" in text and ("HEADER" in text or "ENTITIES" in text or "TABLES" in text):
        return "dxf"
    if "<svg" in text:
        return "svg"
    return "unknown"


def add_elements(sheet, dets, found_by):
    """Put detector output into the sheet: symbols always; rooms/floors only when the sheet has none from text."""
    sheet.setdefault("elements", [])
    have_rooms = bool(sheet.get("rooms"))
    for cls in ("openings", "fixtures", "rooms", "floors"):
        sheet.setdefault(cls, [])
    for i, d in enumerate(dets):
        c = d["cls"]
        e = dict(id=f"{c}{i}", cls=c, bbox=d["bbox"], conf=d["conf"], exact=bool(d.get("exact")))
        if c in ROOM_TYPES:
            if not have_rooms:
                sheet["rooms"].append(e)
            continue
        if c == "floor":
            sheet["floors"].append(e)
            continue
        sheet["openings" if c in OPENINGS else "fixtures"].append(e)
        sheet["elements"].append(dict(id=e["id"], type=c, bbox=d["bbox"], conf=d["conf"],
                                      found_by=found_by + (" + linework" if d.get("exact") else "")))
    if not have_rooms and any(d["cls"] in ROOM_TYPES or d["cls"] == "floor" for d in dets):
        sheet["room_classes_available"] = True
    return sheet


def _iou(a, b):
    iw, ih = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return 0.0 if iw <= 0 or ih <= 0 else iw * ih / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - iw * ih)


def snap_to_linework(dets, quads, classes=("door", "window"), min_iou=0.4):
    """Vector PDFs: the detector says WHERE doors and windows are; the drawing's own linework says exactly what is
    there. Per class:
      1. each detection is matched to the closed four-sided paths that coincide with it;
      2. the drawing style (stroke colour, width, filled or not) most of those paths share is this sheet's style for
         that element;
      3. if the detections and that style agree (most detections hit it, and most paths of that style are
         hit by a detection), the element list becomes ALL paths of that style: exact geometry, and one element per
         path even where two rectangles overlap or the detector missed one;
      4. otherwise each detection only takes the geometry of the paths it coincides with.
    Returns (new detections, notes)."""
    out, notes = [d for d in dets if d["cls"] not in classes], {}
    for cls in classes:
        D = [d for d in dets if d["cls"] == cls]
        if not D:
            continue
        hits = [[i for i, (q, _) in enumerate(quads) if _iou(d["bbox"], q) >= min_iou] for d in D]
        votes = {}
        for h in hits:
            for i in h:
                votes[quads[i][1]] = votes.get(quads[i][1], 0) + 1
        style = max(votes, key=votes.get) if votes else None
        if style is not None:
            matched = {i for h in hits for i in h if quads[i][1] == style}
            group = [i for i, (q, st) in enumerate(quads) if st == style]
            dets_on_style = sum(1 for h in hits if any(quads[i][1] == style for i in h))
            if len(matched) >= 3 and dets_on_style >= 0.8 * len(D) and len(matched) >= 0.8 * len(group):
                conf = sorted(d["conf"] for d in D)[len(D) // 2]
                out += [dict(cls=cls, bbox=[round(v, 1) for v in quads[i][0]], conf=conf, exact=True) for i in group]
                notes[cls] = f"{len(group)} paths drawn in this sheet's {cls} style; the detector marked {dets_on_style} of {len(D)} detections on them"
                continue
        for d, h in zip(D, hits):
            if not h:
                out.append(d)
            for i in h:
                out.append(dict(cls=cls, bbox=[round(v, 1) for v in quads[i][0]], conf=d["conf"], exact=True))
    return out, notes


def raw_detections(path, page, img):
    """Detector output above confidence 0.05, cached on disk per file + weights (detection is the slow step)."""
    import hashlib
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{page}|{os.stat(detect.WEIGHTS).st_mtime_ns}|{bool(detect.tuned().get('tta'))}|{os.environ.get('DRAWQA_TTA')}".encode()).hexdigest()
    cache = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
    f = os.path.join(cache, key + ".json")
    if os.path.exists(f):
        return json.load(open(f))
    dets = detect.detect(img, raw=True)
    os.makedirs(cache, exist_ok=True)
    json.dump(dets, open(f, "w"))
    return dets


def estimate_scale_from_doors(sheet):
    """No printed scale: assume the typical door opening is 0.9 m wide.

    Checked on 65 Swiss plans (1,777 doors with known size): the per-plan median door is 0.88 m; assuming 0.9 m gives a
    median length error of 5% (worst 24%), i.e. areas are typically within about 10%. The answer must say so."""
    doors = sorted(max(o["bbox"][2] - o["bbox"][0], o["bbox"][3] - o["bbox"][1]) for o in sheet.get("openings", []) if o["cls"] == "door")
    if len(doors) < 3:
        return None
    px = doors[len(doors) // 2]
    return dict(m_per_px=0.9 / px, denominator=None, estimated=True, length_error="about 5% typical, up to 25%",
                source=f"estimated: median of {len(doors)} door openings assumed to be 0.9 m wide")


def convert(path, page=0, use_detector=True):
    kind = sniff(path)
    if kind == "pdf":
        from . import pdfsheet
        import pymupdf
        sheet = pdfsheet.load(path, page)
        pg = pymupdf.open(path)[page]
        vector = len(pg.get_drawings()) > 50 or len(sheet["texts"]) > 5
        sheet["kind"] = "vector pdf" if vector else "scanned pdf"
        img = pdfsheet.render(pg)
    elif kind == "image":
        import cv2
        img = cv2.imread(path)
        h, w = img.shape[:2]
        sheet = dict(source=path, source_type="raster", kind="image", units="px", y_flip=False, page=0, width_px=w, height_px=h,
                     scale=dict(m_per_px=None, denominator=None, source="no printed scale found"),
                     texts=[], rooms=[], openings=[], fixtures=[], floors=[], render=dict(type="image", width=w, height=h))
    elif kind == "dxf":
        from . import swiss
        sheet, img = swiss.load(path), None
        sheet["kind"] = "cad dxf"
    elif kind == "ifc":
        from . import ifc
        sheet, img = ifc.load(path), None
        sheet["kind"] = "bim model"
    else:
        return dict(source=path, source_type="unknown", kind=kind, format="drawqa-sheet/1",
                    note=f"File type '{kind}' cannot be read yet.", render=dict(type="none"))
    if img is not None and use_detector and detect.available():
        from .postprocess import apply
        dets = apply(raw_detections(path, page, img), detect.thresholds(), detect.tuned().get("rules"))
        if kind == "pdf" and vector and os.environ.get("DRAWQA_SNAP", "1") == "1":
            dets, sheet["linework_notes"] = snap_to_linework(dets, pdfsheet.quads(pg, pdfsheet.DPI / 72))
        add_elements(sheet, dets, found_by="detector")
        sheet["model"] = "element detector"
        if not (sheet.get("scale") or {}).get("m_per_px"):
            sheet["scale"] = estimate_scale_from_doors(sheet) or sheet.get("scale")
    sheet["format"] = "drawqa-sheet/1"
    return sheet


def save(sheet, out):
    json.dump(sheet, open(out, "w"), indent=1, default=lambda o: list(o) if isinstance(o, (set, tuple)) else str(o))
    return out


if __name__ == "__main__":  # python -m drawqa.convert <file> [out.json]
    import sys
    s = convert(sys.argv[1])
    out = save(s, sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(os.path.basename(sys.argv[1]))[0] + ".sheet.json")
    print(s.get("kind"), "| elements", len(s.get("elements", [])), "| texts", len(s.get("texts", [])), "| rooms", len(s.get("rooms", [])),
          "| scale", (s.get("scale") or {}).get("source"), "->", out)
