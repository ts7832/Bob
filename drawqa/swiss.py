"""swiss_dwellings DXF -> sheet graph (unified format).

Rules validated on the preview set (English questions: 98.6% exact):
  doors    = closed 5-point polylines on layer DOOR (double doors have 2 arcs but 1 opening)
  windows  = consecutive triples of polylines on layer WINDOW
  fixtures = outer rectangle on layer FIXTURE, classified by what is drawn inside it
  rooms    = number label + name above + printed area below; area falls back to the ROOM_OUTLINE polygon
"""
import re
from collections import defaultdict

from .dxf import entities, bbox
from .geom import point_in_polygon, shoelace

OUTDOOR = re.compile(r"^(Balkon|Balcon|Balcone|Loggia|Terrasse|Terrazza|Sitzplatz)$", re.I)
UNIT_LABEL = re.compile(r"^(App\.|Whg\.)\s*([A-Z])$")
ROOM_NO = re.compile(r"^[A-Z]?\d*\.?\d+$")
AREA_TXT = re.compile(r"^([\d.]+)\s*m²$")


def _classify_fixtures(fx):
    rects = [e for e in fx if e["type"] == "LWPOLYLINE" and len(e["xs"]) == 4]
    rects.sort(key=lambda e: -(bbox(e)[2] - bbox(e)[0]) * (bbox(e)[3] - bbox(e)[1]))
    used, out = set(), []
    for R in rects:
        if R["idx"] in used:
            continue
        x0, y0, x1, y1 = bbox(R)
        eps = 0.02
        inner = [e for e in fx if e is not R and e["xs"] and x0 - eps <= bbox(e)[0] and bbox(e)[2] <= x1 + eps
                 and y0 - eps <= bbox(e)[1] and bbox(e)[3] <= y1 + eps]
        L, S = max(x1 - x0, y1 - y0), min(x1 - x0, y1 - y0)
        poly = lambda e: e["type"] == "LWPOLYLINE"
        n_ell = sum(1 for e in inner if poly(e) and 30 <= len(e["xs"]) <= 60)
        n_round = sum(1 for e in inner if poly(e) and len(e["xs"]) > 60)
        n_line = sum(1 for e in inner if poly(e) and len(e["xs"]) == 2)
        hobs = sum(1 for e in inner if e["type"] == "CIRCLE" and 0.05 <= e["r"] <= 0.12)
        big_c = sum(1 for e in inner if e["type"] == "CIRCLE" and e["r"] > 0.15)
        diag = sum(1 for e in inner if poly(e) and len(e["xs"]) == 2
                   and abs(e["xs"][0] - e["xs"][1]) > 0.3 and abs(e["ys"][0] - e["ys"][1]) > 0.3)
        if hobs >= 2: kind = "kitchen_unit"
        elif n_round: kind = "bathtub"
        elif n_ell and n_line: kind = "wc"
        elif n_ell: kind = "washbasin"
        elif big_c: kind = "washing_machine"
        elif n_ell == n_round == n_line == hobs == diag == 0 and 0.65 <= S and L <= 1.05 and L / S < 1.2: kind = "shower"
        elif diag >= 2: kind = "shaft"
        elif n_line >= 4: kind = "stairs"
        else: kind = "other"
        for e in inner:
            if poly(e) and len(e["xs"]) == 4:
                used.add(e["idx"])
        out.append(dict(id=f"fx{R['idx']}", cls=kind, bbox=[x0, y0, x1, y1]))
    return out


def load(path):
    E = entities(path)
    lay = defaultdict(list)
    for e in E:
        lay[e["layer"]].append(e)

    # --- rooms -------------------------------------------------------------
    outlines = [(e["idx"], list(zip(e["xs"], e["ys"]))) for e in lay["ROOM_OUTLINE"] if e["type"] == "LWPOLYLINE"]
    texts = [e for e in lay["ROOM_TEXT"] if e["type"] == "TEXT"]
    rooms, unit_labels = [], []
    for k, e in enumerate(texts):
        s, (x, y) = e["text"], e["pos"]
        m = UNIT_LABEL.match(s)
        if m:
            unit_labels.append((m.group(2), x, y, e["idx"]))
            continue
        if not ROOM_NO.match(s):
            continue
        prev = texts[k - 1]["text"] if k > 0 else ""
        name = prev if prev and not re.search(r"\d", prev) and not UNIT_LABEL.match(prev) else None
        nxt = texts[k + 1]["text"] if k + 1 < len(texts) else ""
        m2 = AREA_TXT.match(nxt)
        label_area = float(m2.group(1)) if m2 else None
        cands = [(oid, P) for oid, P in outlines if point_in_polygon(x, y, P)]
        oid, P = min(cands, key=lambda c: shoelace(c[1])) if cands else (None, None)
        geom_area = round(shoelace(P), 2) if P else None
        rooms.append(dict(id=f"room{e['idx']}", number=s, name=name, polygon=P, outline_id=oid,
                          area_m2=label_area if label_area is not None else geom_area,
                          area_source="printed label" if label_area is not None else "polygon geometry",
                          label_area=label_area, geom_area=geom_area,
                          outdoor=bool(name and OUTDOOR.match(name)), text_pos=[x, y]))

    # --- apartments ----------------------------------------------------------
    units = []
    for e in lay["UNIT_OUTLINE"]:
        if e["type"] != "LWPOLYLINE":
            continue
        P = list(zip(e["xs"], e["ys"]))
        lab = next((L for L, x, y, _ in unit_labels if point_in_polygon(x, y, P)), None)
        units.append(dict(id=f"unit{e['idx']}", label=lab, polygon=P, residential=lab is not None))
    for r in rooms:
        x, y = r["text_pos"]
        r["unit"] = next((u["label"] for u in units if point_in_polygon(x, y, u["polygon"])), None)

    # --- openings ------------------------------------------------------------
    openings = []
    for e in lay["DOOR"]:
        if e["type"] == "LWPOLYLINE" and len(e["xs"]) == 5:
            openings.append(dict(id=f"door{e['idx']}", cls="door", polygon=list(zip(e["xs"], e["ys"])), bbox=list(bbox(e))))
    wins = [e for e in lay["WINDOW"] if e["type"] == "LWPOLYLINE"]
    # a window is drawn as 3 polylines (one 4-point rectangle + two glass lines); the count is len/3.
    # The rectangle is the window's box; fall back to consecutive triples if a file is drawn differently.
    rects = [w for w in wins if len(w["xs"]) == 4]
    if len(rects) == len(wins) // 3:
        for w in rects:
            openings.append(dict(id=f"win{w['idx']}", cls="window", bbox=list(bbox(w)), grouped=True))
    else:
        for i in range(0, len(wins) - 2, 3):
            b = [bbox(w) for w in wins[i:i + 3]]
            bb = [min(v[0] for v in b), min(v[1] for v in b), max(v[2] for v in b), max(v[3] for v in b)]
            openings.append(dict(id=f"win{wins[i]['idx']}", cls="window", bbox=bb, grouped=False))

    fixtures = _classify_fixtures(lay["FIXTURE"])

    # --- render payload (y flipped so the browser can draw it directly) --------
    items = []
    for e in E:
        if not e["xs"] or e["layer"] in ("0", None) and e["type"] != "TEXT":
            continue
        if e["type"] == "LWPOLYLINE":
            closed = any(c == "70" and int(v) & 1 for c, v in e["codes"])
            items.append(dict(k="poly", layer=e["layer"], pts=[[x, -y] for x, y in zip(e["xs"], e["ys"])], closed=closed))
        elif e["type"] == "ARC":
            items.append(dict(k="arc", layer=e["layer"], c=[e["xs"][0], -e["ys"][0]], r=e["r"], a0=e["a0"], a1=e["a1"]))
        elif e["type"] == "CIRCLE":
            items.append(dict(k="circle", layer=e["layer"], c=[e["xs"][0], -e["ys"][0]], r=e["r"]))
        elif e["type"] == "TEXT" and e["layer"] == "ROOM_TEXT":
            x, y = e["pos"]
            items.append(dict(k="text", layer=e["layer"], p=[x, -y], t=e["text"], h=e["height"] or 0.25))
    xs = [p[0] for it in items if it["k"] == "poly" for p in it["pts"]]
    ys = [p[1] for it in items if it["k"] == "poly" for p in it["pts"]]

    if not xs:  # a DXF that does not follow the layer convention this reader knows: say so instead of failing
        import collections
        ents = E
        layers = collections.Counter(e["layer"] for e in ents)
        kinds = collections.Counter(e["type"] for e in ents)
        return dict(source=path, source_type="unknown", kind="cad dxf (unknown layout)", rooms=[], openings=[], fixtures=[], render=dict(type="none"),
                    note=f"This DXF has {len(ents)} entities ({', '.join(f'{n} {k}' for k, n in kinds.most_common(4))}) on layers "
                         f"{', '.join(repr(l) for l, _ in layers.most_common(5))}. The exact DXF reader only knows drawings with DOOR, WINDOW, FIXTURE and "
                         f"ROOM_OUTLINE layers, and block contents are not expanded, so nothing can be counted or measured here yet.")
    return dict(source=path, source_type="vector-dxf", units="m", y_flip=True,
                rooms=rooms, units_list=units, openings=openings, fixtures=fixtures,
                render=dict(type="vector", items=items, bounds=[min(xs), min(ys), max(xs), max(ys)]))
