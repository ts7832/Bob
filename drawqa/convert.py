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
    walls     [{id, polygon, axis, thickness_px, conf, length_m, thickness_m}]   images only: wall segments with their
              centre lines (drawqa/segment.py); doors and windows then carry wall, along_px, width_px, width_m
`openings` / `fixtures` are views of `elements` kept for the question engine.
"""
import json, os, re

from . import detect

OPENINGS = {"door", "window"}
ROOM_TYPES = {"bedroom", "kitchen", "bathroom", "wc_room", "balcony", "sauna_room",
              "room", "living_room", "dining", "entry", "storage", "utility", "dressing_room", "terrace", "garage", "other_room"}


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
                                      found_by=d.get("found_by") or (found_by + (" + linework" if d.get("exact") else ""))))
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
                # a path drawn twice exactly on top of itself is one element on the drawing: count what people see
                uniq = list({tuple(round(v) for v in quads[i][0]): i for i in group}.values())
                out += [dict(cls=cls, bbox=[round(v, 1) for v in quads[i][0]], conf=conf, exact=True) for i in uniq]
                notes[cls] = f"{len(uniq)} {cls}s drawn in this sheet's {cls} style; the detector marked {dets_on_style} of {len(D)} detections on them"
                continue
        seen = set()
        for d, h in zip(D, hits):
            if not h:
                out.append(d)
            for i in h:
                k = tuple(round(v) for v in quads[i][0])
                if k in seen:  # the same path drawn twice
                    continue
                seen.add(k)
                out.append(dict(cls=cls, bbox=[round(v, 1) for v in quads[i][0]], conf=d["conf"], exact=True))
    return out, notes


def raw_detections(path, page, img):
    """Detector output above confidence 0.05, cached on disk per file + weights (detection is the slow step)."""
    import hashlib
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{page}|{os.stat(detect.WEIGHTS).st_mtime_ns}|{bool(detect.tuned().get('tta'))}|{os.environ.get('DRAWQA_TTA')}|{detect.TTA_MAX_TILES}".encode()).hexdigest()
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
    use_detector = use_detector and os.environ.get("DRAWQA_DETECT", "1") == "1"  # 0: text, scale and tables only (light)
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
        from .postprocess import apply, detector_fit
        raw = raw_detections(path, page, img)
        # how the detector ran (drawqa/detect.py): a very large sheet gets one orientation only; said in every answer.
        # Rebuilt from the sheet size, so it is also right when the detections came from the disk cache.
        h_, w_ = img.shape[:2]
        n_tiles = len(detect.tiles(w_, h_)) if max(w_, h_) > detect.TILE * 1.2 else 1
        if (os.environ.get("DRAWQA_TTA") == "1" or detect.tuned().get("tta")) and n_tiles > getattr(detect, "TTA_MAX_TILES", 10 ** 9):
            sheet["detector_note"] = (detect.last_info.get("note") if getattr(detect, "last_info", None) and detect.last_info.get("note") else
                                      f"the detector looked at this sheet in one orientation only ({n_tiles} tiles), while its thresholds "
                                      "were tuned on four, so it may find somewhat fewer symbols than on smaller drawings")
        sheet["detector_fit"] = detector_fit(raw, detect.thresholds())
        dets = apply(raw, detect.thresholds(), detect.tuned().get("rules"))
        if kind == "pdf" and vector and os.environ.get("DRAWQA_SNAP", "1") == "1":
            dets, sheet["linework_notes"] = snap_to_linework(dets, pdfsheet.quads(pg, pdfsheet.DPI / 72))
        if sheet.get("kind") in ("image", "scanned pdf") and os.environ.get("DRAWQA_REPEATS", "1") == "1":
            # the detector can miss every second copy of a window it found elsewhere on the same sheet (a US dormitory
            # plan): exact repeats of its sure windows, checked by the pattern along the wall (drawqa/repeats.py)
            from . import repeats
            more = repeats.complete(img, dets, "window")
            if more:
                dets = dets + more
                sheet["repeat_note"] = (f"{len(more)} windows were added as exact copies of windows the detector found with "
                                        "confidence elsewhere on this sheet (template match plus the pattern along the wall); "
                                        "they are marked found_by 'repeat of a detected window'")
        add_elements(sheet, dets, found_by="detector")
        sheet["model"] = "element detector"
        sheet["detector_classes"] = sorted(detect.thresholds()) or None  # what this detector was trained to find
        if not (sheet.get("scale") or {}).get("m_per_px"):
            sheet["scale"] = estimate_scale_from_doors(sheet) or sheet.get("scale")
    from . import segment, ocr
    if img is not None and sheet.get("kind") in ("image", "scanned pdf"):
        # an image has no text layer: read what is printed on it (tags, notes, names, scale bar), with or without the
        # detector (DRAWQA_DETECT=0 gives a light, text-only sheet)
        if ocr.available() and not sheet.get("texts"):
            sheet["texts"] = [dict(id=f"t{i}", found_by="ocr", **t) for i, t in enumerate(raw_texts(path, page, img))]
        if sheet.get("texts"):
            scale_from_text(sheet, img)  # before outlines: their areas use the scale
        # walls and room outlines: only where the sheet has none of its own. The model was trained on scanned apartment
        # plans read whole, so it is not run on vector sheets, which carry exact outlines anyway.
        if use_detector and segment.available() and os.environ.get("DRAWQA_OUTLINES", "1") == "1":
            if max(img.shape[:2]) > SEG_MAX_SIDE:
                # read whole, the sheet is shrunk to 1280 px: walls vanish and the model draws shapes across rooms
                # (diagonal "walls" on a 10200 px dormitory plan). So walls are read in full-resolution tiles instead,
                # and room outlines are left to the Claude step (tile edges cut rooms)
                add_outlines(sheet, raw_outlines_tiled(path, page, img), img=img)
                sheet["outline_note"] = (f"walls read by the trained model in full-resolution tiles ({max(img.shape[:2])} px sheet); "
                                         f"room outlines on a sheet this large come from the Claude step, not from the model")
            else:
                add_outlines(sheet, raw_outlines(path, page, img), img=img)
    elif img is not None and kind == "pdf" and sheet.get("texts"):  # vector PDF: check the printed scale it read
        scale_from_text(sheet, img, dpi=pdfsheet.DPI)
    if kind == "pdf" and sheet.get("texts"):  # US-style room labels ("BEDROOM" / "218 SF")
        add_printed_room_labels(sheet)
    if kind == "pdf" and vector:
        add_door_arcs(sheet, pg)
    if img is not None:
        drop_legend_symbols(sheet, img)
    sheet["format"] = "drawqa-sheet/1"
    return sheet


SEG_MAX_SIDE = int(os.environ.get("DRAWQA_SEG_MAX_SIDE", "4000"))  # longest sheet side the wall/room model is used on


LEGEND_TITLE = re.compile(r"^(legend|legende|légende|legenda|leyenda|zeichenerkl[äa]rung|symbol legend)\s*:?$", re.I)


def legend_boxes(sheet, img):
    """Where the sheet's legend is: the panel of ruled lines around a 'Legend' / 'Legende' / 'Legenda' title. Without such
    a panel (or if it would cover more than a third of the sheet) only a small area beside and below the title."""
    import cv2
    import numpy as np
    titles = [t for t in sheet.get("texts", []) if t.get("bbox") and LEGEND_TITLE.match(str(t.get("text", "")).strip())]
    if not titles:
        return []
    H, W = img.shape[:2]
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = (g < 128).astype(np.uint8)
    hl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, W // 5), 1)))
    vl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, H // 8))))
    hs = [cv2.boundingRect(c) for c in cv2.findContours(hl, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]]
    vs = [cv2.boundingRect(c) for c in cv2.findContours(vl, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]]
    out = []
    for t in titles:
        x0, y0, x1, y1 = t["bbox"]
        cx, cy, th = (x0 + x1) / 2, (y0 + y1) / 2, max(8, y1 - y0)
        top = max([y + h for x, y, w, h in hs if y + h <= y0 and x <= cx <= x + w], default=None)
        bot = min([y for x, y, w, h in hs if y >= y1 and x <= cx <= x + w], default=H)
        left = max([x + w for x, y, w, h in vs if x + w <= x0 and y <= cy <= y + h], default=0)
        right = min([x for x, y, w, h in vs if x >= x1 and y <= cy <= y + h], default=W)
        box = [left, top, right, bot] if top is not None else None
        if box is None or (box[2] - box[0]) * (box[3] - box[1]) > W * H / 3 or y0 - top > 6 * th:
            box = [x0 - 2 * th, y0 - th, x0 + 28 * th, y0 + 12 * th]  # the legend's own rows, next to its title
        out.append(dict(title=t["text"], bbox=[round(float(v), 1) for v in box]))
    return out


def drop_legend_symbols(sheet, img):
    """A door or WC drawn in the legend is a sample, not an element of the building: leave it out of the counts and
    keep it in sheet['legend_symbols'], so an answer can say so."""
    boxes = legend_boxes(sheet, img)
    if not boxes:
        return
    sheet["legend_boxes"] = boxes
    inside = lambda b: any(L[0] <= (b[0] + b[2]) / 2 <= L[2] and L[1] <= (b[1] + b[3]) / 2 <= L[3] for L in (x["bbox"] for x in boxes))
    detected = {el["id"] for el in sheet.get("elements", []) if str(el.get("found_by", "")).startswith("detector")}
    dropped = []
    for group in ("openings", "fixtures"):
        keep = []
        for e in sheet.get(group, []):
            if e.get("id") in detected and e.get("bbox") and inside(e["bbox"]):
                dropped.append(dict(id=e["id"], cls=e.get("cls"), bbox=e["bbox"]))
            else:
                keep.append(e)
        sheet[group] = keep
    gone = {d["id"] for d in dropped}
    sheet["elements"] = [el for el in sheet.get("elements", []) if el.get("id") not in gone]
    if dropped:
        sheet["legend_symbols"] = dropped
        names = ", ".join(sorted({str(d["cls"]) for d in dropped}))
        sheet["legend_note"] = f"{len(dropped)} symbol(s) drawn in the legend ({names}) are samples and were not counted"


def scale_from_text(sheet, img, dpi=None):
    """Scale from the sheet's own text and linework (drawqa/scalebar.py): the scale bar measured on the image, the
    printed scale with the paper size, and the dimension strings, each checked against the others (on Stoa's four
    example sheets they agree within 0.1%). Replaces the sheet's scale when it has none, when it was only estimated
    (door widths), or when it disagrees by over 2% with a scale-bar / dimension result (e.g. SCALE: 1/4" = 1'-0" read as
    1:4). The boxes it was read from are kept as evidence for every measured answer."""
    try:
        from . import scalebar
        r = scalebar.scale(sheet["texts"], sheet.get("width_px") or img.shape[1], sheet.get("height_px") or img.shape[0], img=img, dpi=dpi)
    except Exception as e:  # never let the scale reader break a conversion
        sheet.setdefault("notes", []).append(f"scale reader failed: {type(e).__name__}: {e}")
        return
    if not r or not r.get("m_per_px"):
        return
    cur = sheet.get("scale") or {}
    if cur.get("m_per_px") and not cur.get("estimated") and abs(cur["m_per_px"] / r["m_per_px"] - 1) <= 0.02:
        cur["cross_check"] = "; ".join(x for x in (cur.get("cross_check"), f"scale bar / dimensions agree: {r.get('source')}") if x)
        return
    if cur.get("m_per_px") and not cur.get("estimated") and r.get("estimated"):
        return  # a printed scale beats a bar whose unit had to be assumed
    ev = [dict(id=f"scale{i}", shape="box", bbox=c["bbox"], label="scale bar" if c["kind"] == "bar" else c.get("text") or c["kind"])
          for i, c in enumerate(r.get("candidates") or []) if c.get("bbox") and c.get("kind") in ("bar", "note")][:4]
    sheet["scale"] = dict(m_per_px=r["m_per_px"], denominator=r.get("denominator"), estimated=bool(r.get("estimated")),
                          source=r.get("source"), cross_check=r.get("check"), length_error=r.get("check") if r.get("estimated") else None,
                          replaced=cur.get("source"), evidence=ev)


def raw_outlines(path, page, img):
    """Segmentation output, cached on disk per file + weights like the detector's."""
    import hashlib
    from . import segment
    st = os.stat(path)
    key = hashlib.sha1(f"seg|{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{page}|{os.stat(segment.WEIGHTS).st_mtime_ns}|{segment.area_bias()}|{segment.FILL}".encode()).hexdigest()
    cache = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
    f = os.path.join(cache, key + ".json")
    if os.path.exists(f):
        return json.load(open(f))
    found = segment.outlines(img)
    os.makedirs(cache, exist_ok=True)
    json.dump(found, open(f, "w"))
    return found


def raw_outlines_tiled(path, page, img):
    """Walls of a big sheet read in native-resolution tiles (segment.outlines_tiled), cached like raw_outlines."""
    import hashlib
    from . import segment
    st = os.stat(path)
    key = hashlib.sha1(f"segtile|{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{page}|{os.stat(segment.WEIGHTS).st_mtime_ns}".encode()).hexdigest()
    cache = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
    f = os.path.join(cache, key + ".json")
    if os.path.exists(f):
        return json.load(open(f))
    found = segment.outlines_tiled(img)
    os.makedirs(cache, exist_ok=True)
    json.dump(found, open(f, "w"))
    return found


def raw_texts(path, page, img):
    """OCR output for an image, cached on disk per file. A large sheet (longer side over textlayer.BIG px, e.g. a
    10200 x 6600 piping plan) is read in native-resolution tiles: whole-image OCR shrinks it and loses most text."""
    import hashlib
    from . import ocr, textlayer
    if max(img.shape[:2]) > textlayer.BIG and page == 0:
        return [{k: v for k, v in t.items() if k != "raw"} for t in textlayer.read(path, img)]
    st = os.stat(path)
    key = hashlib.sha1(f"ocr|{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{page}".encode()).hexdigest()
    cache = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
    f = os.path.join(cache, key + ".json")
    if os.path.exists(f):
        return json.load(open(f))
    T = ocr.texts(img)
    os.makedirs(cache, exist_ok=True)
    json.dump(T, open(f, "w"))
    return T


OUTLINE_SOURCE = "outline found by the segmentation model"
# A room's printed area and its outline must give the same scale within this share of length (twice that on area).
# Chosen by hand, not tuned: on the one preview scan checked in detail, the 10 agreeing rooms were within 4%.
AREA_AGREE = 0.05


def printed_areas(sheet):
    """Room areas printed on an image, from two readers: the vision-model reader's cached result for this file when
    there is one (drawqa/reader.py; this function never calls the service), and OCR for everything it did not read.
    Each is dict(area_m2, bbox, text, by)."""
    from . import ocr
    out = []
    try:
        from . import reader
        hit = (reader.rooms(sheet["source"]) if sheet.get("source") and os.path.exists(sheet["source"])
               and os.environ.get("DRAWQA_READER", "1") == "1" else None)  # DRAWQA_READER=0: OCR only
    except (ImportError, AttributeError):
        hit = None
    for r in hit or []:
        if r.get("area") is not None and r.get("bbox"):
            out.append(dict(area_m2=float(r["area"]), bbox=r["bbox"], text=f"{r.get('label', '')} {r['area']}".strip(), by="vision model", sure=True))
    # the same printed number seen by both readers: the vision model boxes the room's name, OCR boxes the number
    # next to it, so "the same" = equal value and close by (within 12% of the sheet's larger side)
    reach = 0.12 * max(sheet.get("width_px") or 0, sheet.get("height_px") or 0, 400)
    dist = lambda a, b: (((a[0] + a[2]) - (b[0] + b[2])) ** 2 + ((a[1] + a[3]) - (b[1] + b[3])) ** 2) ** 0.5 / 2
    seen = list(out)
    for a in ocr.room_areas(sheet.get("texts", [])):
        if not any(abs(a["area_m2"] - o["area_m2"]) < 0.051 and dist(a["bbox"], o["bbox"]) <= reach for o in seen):
            out.append(dict(a, by="OCR"))
    # US-style labels read by OCR: the room's name over "218 SF" (the m2 readers above do not know square feet)
    try:
        from . import tags
        for x in tags.printed_room_areas(sheet.get("texts", []))["rooms"]:
            m2 = round(x["value"] * SQFT_M2, 6) if x["unit"] == "sqft" else x["value"]
            if not any(abs(m2 - o["area_m2"]) < 0.051 and dist(x["area_bbox"], o["bbox"]) <= reach for o in out):
                out.append(dict(area_m2=m2, bbox=x["area_bbox"], text=f"{x['name']} {x['value']:g} {'SF' if x['unit'] == 'sqft' else 'm2'}",
                                by="OCR", sure=True, name=x["name"], drawing_unit=x["unit"]))
    except Exception:  # a sheet whose labels cannot be paired keeps the readers above
        pass
    return out


def name_saunas(sheet):
    """An untyped outline that holds a detected sauna stove is the sauna room. (The detector rarely finds sauna rooms
    itself: CubiCasa labels most of them as 'Undefined' spaces, 133 'Sauna' spaces against 2,877 stoves.) Without any
    stove, the one untyped outline holding the most sauna benches is. So inferred sauna rooms never outnumber the
    stoves, or one when there are only benches, and the answer-key rule for counting saunas gives the same number."""
    from . import segment
    fx = sheet.get("fixtures", [])
    centre = lambda e: ((e["bbox"][0] + e["bbox"][2]) / 2, (e["bbox"][1] + e["bbox"][3]) / 2)
    stoves = [centre(f) for f in fx if f["cls"] == "sauna_stove"]
    benches = [centre(f) for f in fx if f["cls"] == "sauna_bench"]
    blank = [r for r in sheet.get("rooms", []) if r.get("cls") == "unlabelled" and r.get("polygon")]
    holds = lambda r, pts: sum(1 for p in pts if segment._inside(*p, r["polygon"]))
    if stoves:
        picked = [r for r in blank if holds(r, stoves)]
    else:
        best = max(blank, key=lambda r: holds(r, benches), default=None)
        picked = [best] if best is not None and holds(best, benches) else []
    for r in picked:
        r.update(cls="sauna_room", type_source="inferred: this outline holds a detected sauna " + ("stove" if stoves else "bench"))


OUTDOOR_WORDS = ("parveke", "terassi", "kuisti", "patio", "balcon", "balkon", "terras", "loggia", "veranda", "porch")


def add_rooms_without_outline(sheet, areas):
    """A printed room area that lies in no outline is a room the segmentation model missed (it misses large open
    rooms most often). It still counts in area sums, with its printed value and a source that says it could not be
    checked against an outline."""
    from . import segment
    polys = [r["polygon"] for r in sheet.get("rooms", []) if r.get("polygon")]
    centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    loose = [a for a in areas if not any(segment._inside(*centre(a["bbox"]), P) for P in polys)]
    for i, a in enumerate(loose):
        src = f"printed area read by {a['by']} (no outline found to check it against)"
        cx, cy = centre(a["bbox"])
        boxes = [r for r in sheet["rooms"] if not r.get("polygon") and r.get("area_m2") is None and r.get("bbox")
                 and r["bbox"][0] <= cx <= r["bbox"][2] and r["bbox"][1] <= cy <= r["bbox"][3]]
        if boxes:  # the detector's typed room box for this room: it takes the printed area
            r = min(boxes, key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]))
            r.update(area_m2=a["area_m2"], area_source=src, printed_text=a["text"], outdoor=r.get("cls") in ("balcony", "terrace"),
                     **({"name": a["name"], "drawing_unit": a.get("drawing_unit")} if a.get("name") else {}))
            continue
        sheet["rooms"].append(dict(id=f"printed{i}", cls="unlabelled", bbox=a["bbox"], area_m2=a["area_m2"], printed_text=a["text"],
                                   **({"name": a["name"], "drawing_unit": a.get("drawing_unit")} if a.get("name") else {}),
                                   area_source=src, outdoor=any(w in a["text"].lower() for w in OUTDOOR_WORDS)))


# What common printed room names mean, for a CHECK on the detector's room types (never to count by themselves). Keys are
# normalised: upper case, letters and digits only, Finnish ä/ö folded to A/O. Finnish first (the preview scans).
NAME_MEANING = {}
for _cls, _names in {
    "bedroom": "MH MAKUUH MAKUUHUONE MHUONE BR BEDROOM SCHLAFZIMMER SCHLAFEN CHAMBRE CAMERA SOVEROM SOVRUM",
    "kitchen": "K KK KEITTIO KEITIO KTIO KITCHEN KUCHE KUECHE CUISINE CUCINA KOKKEN",
    "bathroom": "KH KPH PH PESUH PESUHUONE SH BATH BATHROOM BAD BAGNO SDB",
    "wc_room": "WC TOILET TOILETTE",
    "sauna": "SAUNA",
    "living_room": "OH OLOH OLOHUONE LIVING WOHNZIMMER SOGGIORNO SEJOUR STUE",
    "dining": "RUOKAILU RT DINING ESSZIMMER",
    "entry": "ET ETEINEN TK TUULIK TUULIKAAPPI HALL ENTRY FLUR INGRESSO ENTREE",
    "storage": "VH VAR VARASTO KOMERO STORAGE ABSTELLRAUM RIPOSTIGLIO CLOS CLOSET CLO",
    "utility": "KHH KODINHOITOH TEKN LAUNDRY UTILITY WD HVAC MECH MECHANICAL",
    "dressing_room": "PUKUH PUKUHUONE",
    "balcony": "PARVEKE PARV BALCONY BALKON BALCONE BALC",
    "terrace": "TERASSI TERRACE",
}.items():
    for _n in _names.split():
        NAME_MEANING[_n] = _cls


def name_key(label):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(label).upper())
    return "".join(ch for ch in s if ch.isascii() and ch.isalnum())


def name_meaning(label):
    """The room type a printed name stands for: the whole name ('MAKUUH'), else its words ('1BD - BATH - OPTION' ->
    BATH -> bathroom; 'W/D' -> utility). None when no word is known. Words that only qualify ('1BD', 'OPTION') are
    ignored because they are not in the list."""
    whole = NAME_MEANING.get(name_key(label))
    if whole:
        return whole
    words = re.split(r"[\s\-–,()]+", str(label))
    hits = [NAME_MEANING.get(name_key(w)) for w in words if name_key(w)]
    hits = [h for h in hits if h]
    return hits[0] if len(set(hits)) == 1 else None  # two different meanings in one name: say nothing


def ocr_room_names(sheet):
    """Room names read by OCR on an image: name-like text lines grouped when printed under each other in one block
    ("Secondary" / "Classroom" / "(Severe Needs)"), and kept only when the block sits inside a room outline. Dimensions,
    numbers, areas and long notes are not names. -> [dict(label, bbox)] in the vision reader's format."""
    from . import segment, tags
    polys = [r["polygon"] for r in sheet.get("rooms", []) if r.get("polygon")]
    if not polys:
        return []
    T = [t for t in sheet.get("texts", []) if t.get("bbox") and tags._name_like(t["text"])
         and len(t["text"].split()) <= 4 and not re.search(r"\d{2,}", t["text"])]
    T.sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
    blocks = []
    for t in T:
        b = t["bbox"]
        h = b[3] - b[1]
        cx = (b[0] + b[2]) / 2
        for k in blocks:  # the next line of a block: just below it, centred about the same
            kb = k["bbox"]
            if -0.2 * h <= b[1] - kb[3] <= 1.3 * h and abs(cx - (kb[0] + kb[2]) / 2) < 0.75 * max(b[2] - b[0], kb[2] - kb[0]):
                k["label"] += " " + t["text"].strip()
                k["bbox"] = [min(kb[0], b[0]), kb[1], max(kb[2], b[2]), b[3]]
                break
        else:
            blocks.append(dict(label=t["text"].strip(), bbox=list(b)))
    inside = lambda bb: any(segment._inside((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2, [list(p) for p in P]) for P in polys)
    return [k for k in blocks if inside(k["bbox"])]


def attach_printed_names(sheet):
    """Room names printed on an image, as read by the vision model (drawqa/reader.py; only when someone ran it, then
    cached) -> each name goes to the room outline it is printed in (the smallest one), or to the detector's room box when
    the room has no outline. A room can carry several names (a corrected label). Names in no room are kept in
    sheet["printed_names"] with room=None. Cheap and repeatable: it reads one cached file, and it re-attaches only when
    that file changed. A name is a reading, not a measurement: answers built on it are tier "read"."""
    from . import segment
    if sheet.get("source_type") != "raster" or not sheet.get("source") or not os.path.exists(sheet["source"]):
        return
    try:
        from . import reader
        f = reader._key(sheet["source"])
        names = reader.rooms(sheet["source"])
        key = (f, os.path.getmtime(f)) if names is not None else None
    except Exception:
        names, key = None, None
    by = "a vision model"
    if not names and sheet.get("texts"):  # no vision-model reading: the room names OCR read, inside the room outlines
        names, key, by = ocr_room_names(sheet), ("ocr", len(sheet["texts"])), "OCR"
    if not names or sheet.get("_printed_names_key") == key:
        return
    for r in sheet.get("rooms", []):  # a new reading replaces the previous one
        if r.pop("names_from_reader", False):
            r.pop("names", None), r.pop("name", None)
    centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    size = lambda r: (r.get("area_px") or ((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) if r.get("bbox") else 1e18))
    printed = []
    for n in names:
        label = str(n.get("label") or "").strip()
        if not label or not n.get("bbox"):
            continue
        cx, cy = centre(n["bbox"])
        hold = [r for r in sheet.get("rooms", []) if r.get("polygon") and segment._inside(cx, cy, [list(p) for p in r["polygon"]])]
        if not hold:
            hold = [r for r in sheet.get("rooms", []) if not r.get("polygon") and r.get("bbox")
                    and r["bbox"][0] <= cx <= r["bbox"][2] and r["bbox"][1] <= cy <= r["bbox"][3]]
        room = min(hold, key=size) if hold else None
        if room is not None:
            room.setdefault("names", []).append(label)
            room.setdefault("name", label)
            room["names_from_reader"] = True
        printed.append(dict(label=label, bbox=n["bbox"], area=n.get("area"), room=room["id"] if room else None,
                            room_cls=room.get("cls") if room else None, meaning=name_meaning(label)))
    sheet["printed_names"] = printed
    sheet["printed_names_model"] = by
    if by == "OCR":  # OCR also reads notes and title blocks: only names that sit in a room count as room names
        sheet["printed_names"] = [n for n in printed if n["room"] is not None]
    sheet["_printed_names_key"] = key


def add_door_arcs(sheet, pg):
    """Doors from their swing arcs in the PDF's own linework (drawqa/doorarcs.py): exact geometry in any drawing style.
    On the 65 Swiss preview PDFs the count equals the CAD file's distinct door positions on 35 plans and is within one
    door on 59 (1,494 doors against 1,498). Needs the sheet's scale (a door is an arc of door-like radius)."""
    from . import pdfsheet, doorarcs
    mpp = (sheet.get("scale") or {}).get("m_per_px")
    if not mpp:
        return
    try:
        r = doorarcs.doors(pg, mpp * pdfsheet.DPI / 72)
    except Exception as e:
        sheet.setdefault("notes", []).append(f"door arcs failed: {type(e).__name__}: {e}")
        return
    k = pdfsheet.DPI / 72  # PDF points -> sheet pixels
    sheet["door_arcs"] = dict(count=len(r["doors"]), swings=r["swings"],
                              doors=[dict(bbox=[round(v * k, 1) for v in d["bbox_pt"]], width_m=d["radius_m"], double=d["double"])
                                     for d in r["doors"]])


SQFT_M2 = 0.09290304


def add_printed_room_labels(sheet):
    """US-style plans print each room's name with its area under it ("BEDROOM" / "218 SF") and a unit's total in
    brackets beside its tag ("A - U208" / "(762 SF)"): drawqa/tags.printed_room_areas. When the sheet's own reader
    found fewer rooms with an area than that, these become the sheet's rooms (area from the printed label, tier read),
    each tagged with the nearest unit tag when the sheet has several units; unit totals become the units' areas."""
    from . import tags
    r = tags.printed_room_areas(sheet.get("texts") or [])
    # rooms the sheet's own reader found WITH a name and an area (on US sheets it can mistake door-type tags such as
    # U41 / U02 for room numbers and give them outline areas: those have no name and are dropped)
    have = [x for x in sheet.get("rooms", []) if x.get("area_m2") is not None and x.get("name")]
    if len(r["rooms"]) < 2 or len(have) >= len(r["rooms"]):
        return
    to_m2 = lambda v, u: round(v * SQFT_M2, 6) if u == "sqft" else v  # kept exact: answers are converted back to SF
    centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    units = {}
    for t in r["totals"]:
        units.setdefault(t["unit_tag"], t)
    rooms = []
    for i, x in enumerate(r["rooms"]):
        unit = None
        if units:
            cx, cy = centre(x["bbox"])
            unit = min(units.values(), key=lambda t: (centre(t["label_bbox"])[0] - cx) ** 2 + (centre(t["label_bbox"])[1] - cy) ** 2)["unit_tag"]
        rooms.append(dict(id=f"pr{i}", name=x["name"], area_m2=to_m2(x["value"], x["unit"]), area_source="printed label",
                          printed_text=f"{x['name']} {x['value']:g} {'SF' if x['unit'] == 'sqft' else 'm2'}", bbox=x["bbox"],
                          unit=unit, drawing_unit=x["unit"], outdoor=name_meaning(x["name"]) in ("balcony", "terrace"),
                          unit_from="nearest unit tag" if unit and len(units) > 1 else None))
    sheet["rooms"] = have + rooms
    if units:
        sheet["units_list"] = [dict(label=k, residential=True, printed_total_m2=to_m2(t["value"], t["unit"]), drawing_unit=t["unit"],
                                    printed_text=f"{t['label']} ({t['value']:g} {'SF' if t['unit'] == 'sqft' else 'm2'})", bbox=t["bbox"])
                               for k, t in units.items()]
    sheet.setdefault("notes", []).append(f"{len(rooms)} rooms read from printed name/area labels; {len(units)} unit totals")


def calibrate_scale_from_areas(sheet):
    """Images with room areas printed in the rooms.

    Scale: every room whose outline holds exactly one printed area gives its own scale, sqrt(printed m2 / outline
    px2). A reader can misread a digit and an outline can be off, so no single room is trusted: with three or more
    rooms that agree (within AREA_AGREE of their median) the median becomes the sheet's scale and the spread among
    them is its stated error. With fewer, the door-based estimate stays and the printed areas are a cross-check.

    Areas: a room with a printed area takes the printed number (the sum, if its outline holds several: the outline
    then covers several rooms). Its source says whether the outline confirms it. Outlines are wrong more often than a
    printed number is misread (held-out: one outlined room in ten is off by more than 10%), and an outline that leaks
    into the next room would otherwise be counted on top of that room's own printed area."""
    from . import segment
    areas = printed_areas(sheet)
    sure = [a for a in areas if a.get("sure", True)]
    if len(sure) >= 3:  # a number bigger than all the others together is a total (apartment area in a title), not a room
        tot = sum(a["area_m2"] for a in sure)
        big = [a for a in sure if a["area_m2"] > tot - a["area_m2"]]
        if big:
            sheet["printed_total_m2"] = big[0]["area_m2"]
            areas = [a for a in areas if a is not big[0]]
    if not areas:
        return
    add_rooms_without_outline(sheet, [a for a in areas if a.get("sure", True)])  # a bare number with no outline proves nothing
    centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    held = []  # (room, the printed areas inside its outline)
    for r in sheet.get("rooms", []):
        if r.get("polygon") and r.get("area_px"):
            inside = [a for a in areas if segment._inside(*centre(a["bbox"]), r["polygon"])]
            if any(a.get("sure", True) for a in inside):  # numbers read with their unit win over bare ones in the same room
                inside = [a for a in inside if a.get("sure", True)]
            if inside:
                held.append((r, inside))
    pairs = [(r, ins[0], (ins[0]["area_m2"] / r["area_px"]) ** 0.5) for r, ins in held if len(ins) == 1]
    sc = sheet.get("scale") or {}
    doors = sc.get("m_per_px")
    if pairs:
        vals = sorted(p[2] for p in pairs)
        med = vals[len(vals) // 2]
        good = [p for p in pairs if abs(p[2] / med - 1) <= AREA_AGREE]
        if len(good) >= 3 and len(good) >= 0.6 * len(pairs):
            gv = sorted(p[2] for p in good)
            med = gv[len(gv) // 2]
            spread = max(abs(v / med - 1) for v in gv)
            note = f"; the door-based estimate differs by {abs(doors / med - 1) * 100:.0f}%" if doors else ""
            sheet["scale"] = dict(m_per_px=med, denominator=None, estimated=True, length_error=f"the {len(good)} rooms agree within {spread * 100:.0f}%",
                                  source=f"calibrated from {len(good)} printed room areas read by {' and '.join(sorted({a['by'] for _, a, _ in good}))} and matched to room outlines"
                                         + (f" ({len(pairs) - len(good)} more disagreed and were left out)" if len(pairs) > len(good) else "") + note)
        elif doors:
            sc["cross_check"] = (f"{len(pairs)} printed room area(s) were read; their median implies a scale "
                                 f"{abs(med / doors - 1) * 100:.0f}% {'larger' if med > doors else 'smaller'} than the door-based estimate "
                                 f"(too few agreeing rooms to replace it)")
    mpp = (sheet.get("scale") or {}).get("m_per_px")
    confirmed_bare, used = 0, set()
    for r, ins in held:
        printed = round(sum(a["area_m2"] for a in ins), 2)
        by = " and ".join(sorted({a["by"] for a in ins}))
        outline_m2 = round(r["area_px"] * mpp * mpp, 2) if mpp else None
        if outline_m2 is None:
            how = "no scale to check it against its outline"
        elif abs((printed / outline_m2) ** 0.5 - 1) <= AREA_AGREE:
            how = "agrees with its outline"
        else:
            how = f"its outline gives {outline_m2:g} m2, which disagrees"
        if not all(a.get("sure", True) for a in ins):  # bare numbers: an area only if the outline says so
            if how != "agrees with its outline":
                continue
            how = "its unit was not read; taken as the area because it agrees with the outline"
            confirmed_bare += 1
        used.update(id(a) for a in ins)
        r.update(area_m2=printed, area_source=f"printed area read by {by} ({how})", printed_text=" + ".join(a["text"] for a in ins), outline_area_m2=outline_m2)
        if any(a.get("name") for a in ins):  # US labels carry the room name and the sheet's unit
            r.update(name=" + ".join(a["name"] for a in ins if a.get("name")), drawing_unit=ins[0].get("drawing_unit"))
    if confirmed_bare >= 3:  # bare numbers are room areas on this sheet: the ones that found no room are probably rooms too
        loose = [x["area_m2"] for x in areas if not x.get("sure", True) and id(x) not in used]  # in no outline, or in a
        if loose:                                             # room whose outline contradicts them or holds another area
            sheet["unplaced_areas_m2"] = sorted(loose)


def add_outlines(sheet, found, img=None):
    """Walls into the sheet, each door and window attached to its wall, room boxes given their outlines; lengths and
    areas in metres when the sheet has a scale (on an image that scale is itself an estimate, and the answer says so)."""
    from . import segment
    walls = segment.tidy_walls(found["walls"])  # clean shapes first
    if img is not None:  # and no wall running on into blank paper past a corner
        import cv2
        walls = segment.trim_to_ink(walls, img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    walls = segment.merge_collinear(walls)  # one wall drawn as overlapping pieces on one line -> one wall
    walls = [dict(id=f"wall{i}", **w) for i, w in enumerate(walls)]
    openings = sheet.get("openings", [])
    attached = segment.attach_openings(openings, walls)
    runs = {w["id"]: w["axis"] for w in walls}
    perim = segment.wall_perimeters(walls)  # before the cuts: perimeters run straight across doors and windows
    walls = segment.cut_openings(walls, openings)  # the model draws walls through doors and windows; cut them there
    typed = len(sheet.get("rooms", []))
    sheet["rooms"], outlined = segment.outline_rooms(sheet.get("rooms", []), found["rooms"])
    name_saunas(sheet)
    calibrate_scale_from_areas(sheet)
    mpp = (sheet.get("scale") or {}).get("m_per_px")
    if mpp:
        for w in walls:
            x0, y0, x1, y1 = w["axis"]
            w["length_m"], w["thickness_m"] = round(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 * mpp, 2), round(w["thickness_px"] * mpp, 2)
        for o in openings:
            if o.get("width_px"):
                o["width_m"] = round(o["width_px"] * mpp, 2)
        if perim:
            ins = [round(v * mpp, 2) for v in perim["inside_px"]]
            sheet["wall_perimeters"] = dict(outside_m=round(perim["outside_px"] * mpp, 2), inside_m=ins, wall_networks=perim["pieces"],
                                            outside_ring=perim["outside_ring"], inside_rings=perim["inside_rings"])
            sheet["perimeter_note"] = (f"wall perimeters (class 'wall perimeter': the joined walls' outside face and the inside "
                                       f"face of each closed space; cite those items), measured straight across doors and windows: outside face of the walls "
                                       f"{perim['outside_px'] * mpp:.2f} m; inside faces of the {len(ins)} spaces the walls close all "
                                       f"round {sum(ins):.2f} m in all (each: {', '.join(f'{v:.2f}' for v in ins[:30])} m)"
                                       + (f"; the walls form {perim['pieces']} separate pieces, so some wall was missed and a "
                                          "space that is open there has no inside perimeter" if perim["pieces"] > 1 else ""))
        cut = sum(1 for o in openings if o.get("wall") is not None and o.get("width_px"))
        if cut:
            solid = sum(w["length_m"] for w in walls)
            through = sum(((a[2] - a[0]) ** 2 + (a[3] - a[1]) ** 2) ** 0.5 for a in runs.values()) * mpp
            sheet["wall_note"] = (f"walls are cut at the {cut} doors and windows that sit in them, so wall lengths are solid "
                                  f"wall only ({solid:.2f} m in all); measured through the openings the same walls run "
                                  f"{through:.2f} m")
        for r in sheet["rooms"]:
            if r.get("area_px") and r.get("area_m2") is None:
                r["area_m2"], r["area_source"] = round(r["area_px"] * mpp * mpp, 2), OUTLINE_SOURCE
    by_id = {o["id"]: o for o in openings}
    for e in sheet.get("elements", []):  # the unified element list carries the same wall attachment
        o = by_id.get(e["id"])
        if o and "wall" in o:
            e.update({k: o[k] for k in ("wall", "along_px", "width_px", "width_m") if k in o})
    sheet["walls"] = walls
    sheet["wall_outline"] = segment.wall_outline(walls)  # for drawing: all walls as one connected shape
    sheet["outline_notes"] = dict(model="wall and room-outline segmentation model", walls=len(walls),
                                  openings_attached=f"{attached} of {len(openings)}",
                                  rooms_outlined=f"{outlined} of {typed} typed rooms",
                                  untyped_outlines=len(sheet["rooms"]) - typed, measured=segment.measured())
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
