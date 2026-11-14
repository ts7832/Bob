"""Rank drawings in the dataset preview as gold-set candidates: python tools/gold_pick.py [--out gold/candidates.csv]

A good candidate looks like a judge's file: a real drawing sheet (vector PDF with a text layer, or a large scan),
dense with content, and ideally one the dataset has no questions for, so nobody has tuned on it. The 241 preview
scans and 65 preview Swiss plans are left out (they are the other scoreboard's test sets).

Columns: source, path, format, pages, page size in inches, rotation, words on page 1, images on page 1,
pixels (raster), questions in the dataset for this file, score (higher = better candidate).
"""
import argparse, csv, glob, json, os

ROOT = os.path.join(os.path.dirname(__file__), "..", "junction-dataset-preview")
SKIP_SOURCES = ("cubicasa5k", "swiss_dwellings")  # test sets of drawqa/eval.py
RASTER = (".png", ".jpg", ".jpeg")


def describe(path):
    ext = os.path.splitext(path)[1].lower()
    row = dict(format=ext.lstrip("."), pages=1, size_in="", rotation=0, words=0, images=0, pixels=0)
    if ext == ".pdf":
        import pymupdf
        with pymupdf.open(path) as d:
            pg = d[0]
            row.update(pages=len(d), size_in=f"{pg.rect.width / 72:.0f}x{pg.rect.height / 72:.0f}",
                       rotation=pg.rotation, words=len(pg.get_text("words")), images=len(pg.get_images()))
    elif ext in RASTER:
        from PIL import Image
        with Image.open(path) as im:
            row["pixels"] = im.width * im.height
    return row


def score(row):
    """Text-dense vector sheets first, then large scans; files with no dataset questions get a bonus (unseen)."""
    s = 0.0
    if row["format"] == "pdf":
        s += min(row["words"], 4000) / 400  # up to 10 for a dense sheet
        s += 2 if row["words"] == 0 else 0  # a PDF without text is a scan inside a PDF: also worth one or two
    elif row["format"] in ("png", "jpg", "jpeg"):
        s += min(row["pixels"] / 1e6, 8)  # megapixels, up to 8
    if row["questions"] == 0:
        s += 3
    return round(s, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "gold", "candidates.csv"))
    a = ap.parse_args()
    qs = {}
    with open(os.path.join(ROOT, "questions.jsonl"), encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)["path"]
            qs[p] = qs.get(p, 0) + 1
    rows = []
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "**", "*"), recursive=True)):
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        src = rel.split("/")[1]
        ext = os.path.splitext(path)[1].lower()
        if src in SKIP_SOURCES or ext not in (".pdf",) + RASTER or not os.path.isfile(path):
            continue
        try:
            row = describe(path)
        except Exception as e:  # report and carry on: one broken file should not hide the rest
            print(f"cannot open {rel}: {type(e).__name__}: {e}")
            continue
        row.update(source=src, path=rel, questions=qs.get(rel, 0))
        row["score"] = score(row)
        rows.append(row)
    rows.sort(key=lambda r: -r["score"])
    cols = ["score", "source", "format", "pages", "size_in", "rotation", "words", "images", "pixels", "questions", "path"]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r[c] for c in cols} for r in rows)
    print(f"{len(rows)} candidates -> {a.out}")
    for r in rows[:15]:
        print(f"{r['score']:6.2f}  {r['source']:15s} {r['format']:4s} words={r['words']:5d} q={r['questions']:3d}  {r['path'][-70:]}")


if __name__ == "__main__":
    main()
