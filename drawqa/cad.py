"""Any DXF or DWG -> one vector PDF page with a text layer, so the vector-PDF reader takes it from there.

    to_pdf(path) -> dict(pdf=<cached PDF path>, m_per_pt, units, texts, entities, source)   or raises CadError(reason)

Why a PDF: everything downstream already reads vector PDFs (text, printed scale, room outlines, tables, the detector
on the rendered page, snapping to linework). Drawing the CAD file onto a page reuses all of it for any layer naming.
  DWG -> DXF : LibreDWG's dwg2dxf (brew install libredwg).
  DXF -> PDF : ezdxf's drawing add-on with its PyMuPDF backend; blocks are expanded, hatches drawn as outlines.
  text       : ezdxf draws text as outlines, so every TEXT / MTEXT / attribute (also inside blocks) is written again
               as an invisible text layer at its own position. The PDF then has real, searchable text.
  scale      : the page is the model space fitted to a fixed long side; metres per PDF point follow exactly from the
               file's own drawing units ($INSUNITS). None when the file declares no units.
Model space only (layouts / paper space are not drawn). Results are cached in data/cache.
"""
import hashlib, json, os, shutil, subprocess

CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
PAGE_MM = 820.0  # long side of the page the model space is fitted to
UNIT_M = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 8: 2.54e-8, 9: 2.54e-5, 10: 0.9144, 14: 0.1}  # $INSUNITS -> metres
UNIT_NAME = {1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres", 6: "metres", 10: "yards", 14: "decimetres"}


class CadError(Exception):
    pass


def _key(path):
    st = os.stat(path)
    return hashlib.sha1(f"cad2|{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()


def is_dwg(path):
    with open(path, "rb") as f:
        return f.read(4) == b"AC10"


def dwg_to_dxf(path, timeout=300):
    exe = shutil.which("dwg2dxf") or "/opt/homebrew/bin/dwg2dxf"
    if not os.path.exists(exe):
        raise CadError("DWG files need the dwg2dxf converter (brew install libredwg), which is not installed")
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, "cad_" + _key(path) + ".dxf")
    if not os.path.exists(out):
        try:
            subprocess.run([exe, "-y", "-o", out, path], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise CadError(f"converting the DWG took longer than {timeout} s")
        if not os.path.exists(out) or os.path.getsize(out) < 200:
            raise CadError("the DWG could not be converted to DXF (dwg2dxf produced nothing)")
    return out


def _texts(msp):
    """(x, y, height, rotation, text) for every text in model space, block contents included, in drawing units."""
    from ezdxf.disassemble import recursive_decompose
    out = []

    def add(e):
        t = e.dxftype()
        try:
            if t == "TEXT" or t == "ATTRIB":
                p = e.dxf.insert if not e.dxf.hasattr("align_point") or (e.dxf.get("halign", 0) == 0 and e.dxf.get("valign", 0) == 0) else e.dxf.align_point
                s = e.plain_text() if hasattr(e, "plain_text") else e.dxf.text
                if s and s.strip():
                    out.append((p.x, p.y, e.dxf.get("height", 1.0), e.dxf.get("rotation", 0.0), s.strip()))
            elif t == "MTEXT":
                h = e.dxf.get("char_height", 1.0)
                lines = [l for l in e.plain_text().split("\n")]
                top = e.dxf.get("attachment_point", 1) in (1, 2, 3)
                for i, line in enumerate(lines):
                    if line.strip():  # insert point is the top (or the baseline area) of the first line
                        out.append((e.dxf.insert.x, e.dxf.insert.y - (i + (1 if top else 0)) * 1.4 * h + (0.4 * h if top else 0), h, e.dxf.get("rotation", 0.0), line.strip()))
        except Exception:
            pass

    for e in recursive_decompose(msp):
        add(e)
    for ins in msp.query("INSERT"):
        for a in getattr(ins, "attribs", []):
            add(a)
    return out


def _robust_extents(msp, bbox):
    """The drawing's extents without far-away strays (an image or a point kilometres off would shrink the real plan to
    a dot): entity centres outside the 1st-99th percentile band, widened by half its size, are left out."""
    full = bbox.extents(msp, fast=True)
    boxes = []
    for e in msp:
        try:
            b = bbox.extents([e], fast=True)
        except Exception:
            continue
        if b.has_data:
            boxes.append((e, b))
    if len(boxes) < 20:
        return full, set()
    xs = sorted(b.center.x for _, b in boxes); ys = sorted(b.center.y for _, b in boxes)
    q = lambda v, p: v[min(len(v) - 1, max(0, int(p * len(v))))]
    x0, x1, y0, y1 = q(xs, 0.01), q(xs, 0.99), q(ys, 0.01), q(ys, 0.99)
    mx, my = 0.5 * (x1 - x0), 0.5 * (y1 - y0)
    out, dropped = bbox.BoundingBox(), set()
    for e, b in boxes:
        if x0 - mx <= b.center.x <= x1 + mx and y0 - my <= b.center.y <= y1 + my:
            out.extend([b.extmin, b.extmax])
        else:
            dropped.add(e.dxf.handle)
    return (out, dropped) if out.has_data else (full, set())


def to_pdf(path):
    import ezdxf  # noqa: F401
    import pymupdf
    from ezdxf import bbox, recover
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend

    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, "cad_" + _key(path) + ".pdf")
    side = out[:-4] + ".json"
    if os.path.exists(out) and os.path.exists(side):
        return json.load(open(side))
    dxf = dwg_to_dxf(path) if is_dwg(path) else path
    try:
        doc, _ = recover.readfile(dxf)
    except Exception as e:
        raise CadError(f"the CAD file could not be read ({type(e).__name__}: {str(e)[:120]})")
    msp = doc.modelspace()
    ext, strays = _robust_extents(msp, bbox)
    if not ext.has_data or max(ext.size.x, ext.size.y) <= 0:
        raise CadError(f"the CAD file's model space is empty ({len(msp)} entities; drawings kept only in paper-space layouts are not read)")
    k = PAGE_MM / max(ext.size.x, ext.size.y)  # millimetres on the page per drawing unit
    backend = PyMuPdfBackend()
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE, color_policy=config.ColorPolicy.BLACK,
                               hatch_policy=config.HatchPolicy.SHOW_OUTLINE)
    Frontend(RenderContext(doc), backend, config=cfg).draw_layout(msp, filter_func=lambda e: e.dxf.handle not in strays)
    pdf = backend.get_pdf_bytes(layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(0)), settings=layout.Settings(fit_page=False, scale=k))
    d = pymupdf.open("pdf", pdf)
    pg = d[0]
    s = k * 72 / 25.4  # PDF points per drawing unit
    cx, cy = (ext.extmin.x + ext.extmax.x) / 2, (ext.extmin.y + ext.extmax.y) / 2
    texts = _texts(msp)
    n = 0
    for x, y, h, rot, t in texts:
        px, py = (x - cx) * s + pg.rect.width / 2, (cy - y) * s + pg.rect.height / 2
        if not (-5 <= px <= pg.rect.width + 5 and -5 <= py <= pg.rect.height + 5):
            continue
        try:
            pg.insert_text((px, py), t, fontsize=min(200.0, max(1.0, h * s)), rotate=int(round(rot / 90.0)) % 4 * 90, render_mode=3)
            n += 1
        except Exception:
            pass
    d.save(out, deflate=True)
    units = doc.header.get("$INSUNITS", 0)
    info = dict(pdf=out, source=os.path.abspath(path), was_dwg=dxf != path, entities=len(msp), texts=n,
                units=UNIT_NAME.get(units), m_per_pt=(UNIT_M[units] / s) if units in UNIT_M else None,
                page_pt=[round(pg.rect.width, 1), round(pg.rect.height, 1)])
    json.dump(info, open(side, "w"))
    return info


if __name__ == "__main__":  # python -m drawqa.cad <file.dwg | file.dxf>
    import sys
    try:
        print(to_pdf(sys.argv[1]))
    except CadError as e:
        print("cannot read:", e)
