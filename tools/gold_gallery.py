"""Failure gallery for a gold-set run: python tools/gold_gallery.py [--report gold/reports/<stamp>.json] [--all]

One card per question: the question, expected vs given answer, outcome, honesty tier and its basis, and the sheet
with two kinds of marks drawn on it:
  green / red boxes   the system's own evidence (green when the answer was right, red when it was wrong)
  blue boxes          the numbers named in the gold line's evidence_note, found in the PDF's text layer: where a
                      person checking the expected answer should look
Each card has a crop around the marks and a thumbnail of the whole sheet. Cards for wrong answers come first;
--all also includes correct ones (useful while checking draft answers).

Writes gold/reports/<stamp>-gallery/index.html, one self-contained file (images embedded); click an image to enlarge. Coordinates: page pixels at drawqa.pdfsheet.DPI
for PDFs (what drawqa's evidence uses), image pixels for raster files. Evidence from a DXF or BIM model is not drawn.
"""
import argparse, glob, html, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
ROOT = os.path.join(REPO, "junction-dataset-preview")
REPORTS = os.path.join(REPO, "gold", "reports")
DRAWN_SOURCE_TYPES = ("vector-pdf", "raster")  # evidence boxes are in the coordinates of the rendered file only for these
ORDER = ["wrong_confident", "missed_refusal", "wrong_flagged", "false_refusal", "error", "correct"]
GREEN, RED, BLUE = (60, 160, 40), (40, 40, 220), (220, 120, 20)  # BGR


def page_image(path, page_no=1):
    """(BGR image, pymupdf page or None). PDFs are rendered exactly as drawqa renders them for evidence.
    page_no is 1-based (the gold line's `page`, default the first page)."""
    import cv2
    import numpy as np
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pymupdf
        from drawqa import pdfsheet
        page = pymupdf.open(path)[page_no - 1]
        return pdfsheet.render(page), page
    if ext in (".png", ".jpg", ".jpeg"):
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)  # imread fails on non-ASCII Windows paths
        if img is None:
            raise ValueError(f"cannot decode image {path}")
        return img, None
    return None, None


def expected_marks(page, note):
    """Boxes (page pixels) of words on the page that equal a number named in the evidence note."""
    if page is None or not note:
        return []
    from drawqa import pdfsheet
    import pymupdf
    nums = {n for n in re.findall(r"\d[\d,]*(?:\.\d+)?", note) if len(n.replace(",", "")) >= 2}
    if not nums:
        return []
    k = pdfsheet.DPI / 72
    M = page.rotation_matrix
    out = []
    words = page.get_text("words", sort=True)
    for i, (x0, y0, x1, y1, w, *_) in enumerate(words):
        # a number that is part of a dimension (39 1/4", 3'-0", 7'11") is a measurement, not the value we look for
        nxt = words[i + 1][4] if i + 1 < len(words) and abs(words[i + 1][1] - y0) < (y1 - y0) / 2 else ""
        if re.search(r"['\"]", w) or re.match(r"^\d+/\d+", nxt) or nxt[:1] in ("'", '"', "-"):
            continue
        if w.strip("()[],;:") in nums:
            r = pymupdf.Rect(x0, y0, x1, y1) * M
            r.normalize()
            out.append([r.x0 * k, r.y0 * k, r.x1 * k, r.y1 * k])
    return out[:200]


def draw(img, boxes, color, width):
    import cv2
    for b in boxes:
        x0, y0, x1, y1 = (int(round(v)) for v in b)
        cv2.rectangle(img, (x0 - 3, y0 - 3), (x1 + 3, y1 + 3), color, width)


def crop_around(img, boxes, pad=160, max_w=1400):
    import cv2
    h, w = img.shape[:2]
    if boxes:
        x0 = max(0, int(min(b[0] for b in boxes)) - pad)
        y0 = max(0, int(min(b[1] for b in boxes)) - pad)
        x1 = min(w, int(max(b[2] for b in boxes)) + pad)
        y1 = min(h, int(max(b[3] for b in boxes)) + pad)
        img = img[y0:y1, x0:x1]
    if img.shape[1] > max_w:
        img = cv2.resize(img, (max_w, int(img.shape[0] * max_w / img.shape[1])), interpolation=cv2.INTER_AREA)
    return img


def jpg_data_uri(img, quality):
    """The image as a data: URI, so index.html is one self-contained file (viewers that open only the HTML file,
    such as a snapshot preview or an e-mail attachment, would not load JPEGs placed next to it)."""
    import base64
    import cv2
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("cannot encode a gallery image")
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", help="a gold/reports/<stamp>.json (default: the newest)")
    ap.add_argument("--all", action="store_true", help="include correct answers")
    a = ap.parse_args()
    report = a.report or max(glob.glob(os.path.join(REPORTS, "*.json")), default=None, key=os.path.getmtime)
    if not report:
        raise SystemExit("no report in gold/reports; run tools/gold_eval.py first")
    data = json.load(open(report, encoding="utf-8"))
    gold = {}
    with open(os.path.join(REPO, "gold", "gold.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                q = json.loads(line)
                gold[q["id"]] = q
    results = [r for r in data["results"] if a.all or r["outcome"] != "correct"]
    results.sort(key=lambda r: (ORDER.index(r["outcome"]) if r["outcome"] in ORDER else 99, r["id"]))
    if "evidence" not in (data["results"][0] if data["results"] else {}):
        print("note: this report predates evidence boxes in gold_eval.py; only the expected-value marks are drawn")
    out_dir = os.path.splitext(report)[0] + "-gallery"
    os.makedirs(out_dir, exist_ok=True)

    cache, cards = {}, []
    for r in results:
        q = gold.get(r["id"], {})
        path = os.path.join(ROOT, r["path"])
        page_no = int(q.get("page") or 1)
        key = (path, page_no)
        if key not in cache:
            try:
                cache = {key: page_image(path, page_no)}  # one sheet at a time: full-size renders are large
            except Exception as e:
                print(f"{r['id']}: cannot render {r['path']} page {page_no}: {type(e).__name__}: {e}")
                cache = {key: (None, None)}
        img, page = cache[key]
        crop_file = thumb_file = None
        # drawqa reads the first page of a PDF, so its evidence boxes only belong on page 1
        on_sheet = r.get("source_type") in DRAWN_SOURCE_TYPES and page_no == 1
        sys_boxes = [e["bbox"] for e in r.get("evidence", [])] if on_sheet else []
        exp_boxes = expected_marks(page, q.get("evidence_note"))
        if img is not None:
            canvas = img.copy()
            draw(canvas, exp_boxes, BLUE, 4)
            draw(canvas, sys_boxes, GREEN if r["value_ok"] else RED, 5)
            crop_file = jpg_data_uri(crop_around(canvas, sys_boxes + exp_boxes, max_w=1200), 78)
            thumb_file = jpg_data_uri(crop_around(canvas, [], max_w=700), 70)
        cards.append(card(r, q, crop_file, thumb_file, len(sys_boxes), len(exp_boxes)))
        print(f"{r['id']} {r['outcome']}")
    s = data["summary"]
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(PAGE.replace("{{TITLE}}", html.escape(os.path.basename(report)))
                .replace("{{SUMMARY}}", html.escape(f"{s['correct']}/{s['n']} correct ({100 * s['accuracy']:.1f}%)"
                                                    + (f", {s['drafts']} scored against unchecked drafts" if s.get("drafts") else "")
                                                    + f" · tier as expected {s['tier_match']} · llm {'on' if s.get('llm') else 'off'}"))
                .replace("{{CARDS}}", "\n".join(cards)))
    print(f"\n{len(cards)} cards -> {os.path.relpath(os.path.join(out_dir, 'index.html'), REPO)}")


def fmt(v, unit):
    if v is None:
        return "cannot determine"
    return f"{v} {unit or ''}".strip()


def card(r, q, crop, thumb, n_sys, n_exp):
    e = html.escape
    status = q.get("status", "?")
    marks = []
    if n_sys:
        marks.append(f"<span class={'k-ok' if r['value_ok'] else 'k-sys'}>■</span> {n_sys} evidence box(es) from the system")
    if n_exp:
        marks.append(f"<span class=k-exp>■</span> {n_exp} word(s) matching numbers in the evidence note")
    imgs = (f'<img class=crop src="{crop}" alt="crop around the marks" title="click to enlarge">'
            f'<img class=thumb src="{thumb}" alt="whole sheet" title="click to enlarge">') if crop else "<p class=muted>No image for this file type.</p>"
    why = r.get("error") or r.get("explanation") or ""
    return f"""<article class="card" data-outcome="{e(r['outcome'])}" data-origin="{e(r.get('origin') or '')}">
  <header><b>{e(r['id'])}</b> <span class="badge o-{e(r['outcome'])}">{e(r['outcome'].replace('_', ' '))}</span>
    <span class=badge>tier: {e(str(r.get('tier')))}</span> <span class=badge>{e(r.get('origin') or '')}</span>
    <span class="badge s-{e(status)}">{e(status)}{' (draft)' if r.get('draft') else ''}</span></header>
  <p class=q>{e(r['question'])}</p>
  <div class=ans><div><small>expected</small><strong>{e(fmt(r['expected'], r.get('unit')))}</strong></div>
    <div><small>system</small><strong>{e(fmt(r['pred'], r.get('pred_unit')))}</strong></div>
    <div><small>expected tier</small><strong>{e(str(r.get('expected_tier') or '-'))}</strong></div></div>
  <dl><dt>system says</dt><dd>{e(why)}</dd>
    <dt>tier basis</dt><dd>{e(r.get('basis') or '-')}</dd>
    <dt>where to check</dt><dd>{e(q.get('evidence_note') or '-')}</dd>
    <dt>file</dt><dd class=path>{e(r['path'])}</dd>{f"<dt>scorer note</dt><dd>{e(r['note'])}</dd>" if r.get('note') else ''}</dl>
  <p class=legend>{' · '.join(marks) or 'No marks: the system gave no positioned evidence and the note names no number on the page.'}</p>
  <div class=imgs>{imgs}</div>
</article>"""


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Gold failure gallery</title><style>
:root{--bg:#f6f6f4;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#deded8;--red:#b3261e;--amber:#9a6700;--green:#2e7d32;--blue:#1565c0}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--muted:#a3a39d;--card:#20201e;--line:#34342f;--red:#f2b8b5;--amber:#f0c36b;--green:#9ccc9f;--blue:#90caf9}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}h1{font-size:20px;margin:8px 0 2px}.muted{color:var(--muted)}
.bar{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 16px}.bar button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:4px 12px;cursor:pointer}
.bar button.on{border-color:var(--fg);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:0 0 16px}
.badge{display:inline-block;border:1px solid var(--line);border-radius:6px;padding:0 6px;font-size:12px;margin-left:4px;color:var(--muted)}
.o-wrong_confident,.o-missed_refusal,.o-error{color:var(--red);border-color:var(--red)}.o-wrong_flagged,.o-false_refusal{color:var(--amber);border-color:var(--amber)}
.o-correct{color:var(--green);border-color:var(--green)}.s-verified{color:var(--green)}
.q{font-size:16px;margin:8px 0}.ans{display:flex;flex-wrap:wrap;gap:24px;margin:6px 0 10px}.ans small{display:block;color:var(--muted);font-size:12px}
dl{display:grid;grid-template-columns:120px 1fr;gap:2px 12px;margin:0 0 8px;font-size:13px}dt{color:var(--muted)}dd{margin:0}.path{word-break:break-all;color:var(--muted)}
.legend{font-size:12px;color:var(--muted)}.k-sys{color:var(--red)}.k-ok{color:var(--green)}.k-exp{color:var(--blue)}
.imgs{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-start}.imgs img{max-width:100%;border:1px solid var(--line);border-radius:6px;background:#fff}
.crop{width:min(100%,760px)}.thumb{width:min(100%,360px)}.imgs img{cursor:zoom-in}.imgs img.big{width:100%;cursor:zoom-out}
@media (max-width:640px){dl{grid-template-columns:1fr}.ans{gap:14px}}
</style></head><body><main>
<h1>Gold failure gallery</h1><p class=muted>{{TITLE}} · {{SUMMARY}}</p>
<div class=bar id=bar></div>
{{CARDS}}
</main><script>
const cards=[...document.querySelectorAll('.card')],bar=document.getElementById('bar');
const outcomes=['all',...new Set(cards.map(c=>c.dataset.outcome))];let on='all';
function show(){cards.forEach(c=>c.hidden=!(on==='all'||c.dataset.outcome===on));[...bar.children].forEach(b=>b.classList.toggle('on',b.dataset.o===on))}
outcomes.forEach(o=>{const b=document.createElement('button');b.dataset.o=o;const n=o==='all'?cards.length:cards.filter(c=>c.dataset.outcome===o).length;
b.textContent=o.replace(/_/g,' ')+' ('+n+')';b.onclick=()=>{on=o;show()};bar.appendChild(b)});show();
document.querySelectorAll('.imgs img').forEach(i=>i.onclick=()=>i.classList.toggle('big'));
</script></body></html>
"""

if __name__ == "__main__":
    main()
