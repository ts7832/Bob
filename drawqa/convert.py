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
import json, os

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
        from .postprocess import apply, detector_fit
        raw = raw_detections(path, page, img)
        sheet["detector_fit"] = detector_fit(raw, detect.thresholds())
        dets = apply(raw, detect.thresholds(), detect.tuned().get("rules"))
        if kind == "pdf" and vector and os.environ.get("DRAWQA_SNAP", "1") == "1":
            dets, sheet["linework_notes"] = snap_to_linework(dets, pdfsheet.quads(pg, pdfsheet.DPI / 72))
        add_elements(sheet, dets, found_by="detector")
        sheet["model"] = "element detector"
        sheet["detector_classes"] = sorted(detect.thresholds()) or None  # what this detector was trained to find
        if not (sheet.get("scale") or {}).get("m_per_px"):
            sheet["scale"] = estimate_scale_from_doors(sheet) or sheet.get("scale")
    from . import segment
    # walls and room outlines: only where the sheet has none of its own (images, scanned PDFs). The model was trained
    # on scanned apartment plans read whole, so it is not run on vector sheets, which carry exact outlines anyway.
    if img is not None and use_detector and segment.available() and sheet.get("kind") in ("image", "scanned pdf") \
            and os.environ.get("DRAWQA_OUTLINES", "1") == "1":
        from . import ocr
        if ocr.available() and not sheet.get("texts"):  # an image has no text layer: read what is printed on it
            sheet["texts"] = [dict(id=f"t{i}", found_by="ocr", **t) for i, t in enumerate(raw_texts(path, page, img))]
        scale_from_text(sheet, img)  # before outlines: their areas use the scale
        add_outlines(sheet, raw_outlines(path, page, img))
    elif img is not None and kind == "pdf" and sheet.get("texts"):  # vector PDF: check the printed scale it read
        scale_from_text(sheet, img, dpi=pdfsheet.DPI)
    sheet["format"] = "drawqa-sheet/1"
    return sheet


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
            r.update(area_m2=a["area_m2"], area_source=src, printed_text=a["text"], outdoor=r.get("cls") in ("balcony", "terrace"))
            continue
        sheet["rooms"].append(dict(id=f"printed{i}", cls="unlabelled", bbox=a["bbox"], area_m2=a["area_m2"], printed_text=a["text"],
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
    "storage": "VH VAR VARASTO KOMERO STORAGE ABSTELLRAUM RIPOSTIGLIO",
    "utility": "KHH KODINHOITOH TEKN LAUNDRY UTILITY",
    "dressing_room": "PUKUH PUKUHUONE",
    "balcony": "PARVEKE PARV BALCONY BALKON BALCONE",
    "terrace": "TERASSI TERRACE",
}.items():
    for _n in _names.split():
        NAME_MEANING[_n] = _cls


def name_key(label):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(label).upper())
    return "".join(ch for ch in s if ch.isascii() and ch.isalnum())


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
                            room_cls=room.get("cls") if room else None, meaning=NAME_MEANING.get(name_key(label))))
    sheet["printed_names"] = printed
    sheet["printed_names_model"] = "a vision model"
    sheet["_printed_names_key"] = key


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
    if confirmed_bare >= 3:  # bare numbers are room areas on this sheet: the ones that found no room are probably rooms too
        loose = [x["area_m2"] for x in areas if not x.get("sure", True) and id(x) not in used]  # in no outline, or in a
        if loose:                                             # room whose outline contradicts them or holds another area
            sheet["unplaced_areas_m2"] = sorted(loose)


def add_outlines(sheet, found):
    """Walls into the sheet, each door and window attached to its wall, room boxes given their outlines; lengths and
    areas in metres when the sheet has a scale (on an image that scale is itself an estimate, and the answer says so)."""
    from . import segment
    walls = [dict(id=f"wall{i}", **w) for i, w in enumerate(found["walls"])]
    openings = sheet.get("openings", [])
    attached = segment.attach_openings(openings, walls)
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
        for r in sheet["rooms"]:
            if r.get("area_px") and r.get("area_m2") is None:
                r["area_m2"], r["area_source"] = round(r["area_px"] * mpp * mpp, 2), OUTLINE_SOURCE
    by_id = {o["id"]: o for o in openings}
    for e in sheet.get("elements", []):  # the unified element list carries the same wall attachment
        o = by_id.get(e["id"])
        if o and "wall" in o:
            e.update({k: o[k] for k in ("wall", "along_px", "width_px", "width_m") if k in o})
    sheet["walls"] = walls
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
