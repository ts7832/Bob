# DrawQA: Junction 2026 Stoa challenge

Answers natural-language questions about construction drawings and BIM models. Every answer is highlighted on the drawing so a person can check it.

## Run

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install ifcopenshell pymupdf modal polars && .venv/bin/pip install --no-deps ultralytics==8.4.212 ultralytics-thop   # once
PORT=8765 .venv/bin/python demo/server.py        # demo  -> http://localhost:8765
.venv/bin/python -m drawqa.eval                  # scoreboard on questions.jsonl (all languages)
.venv/bin/python -m drawqa.eval --source swiss_dwellings
```

The dataset is expected in `junction-dataset-preview/` (git-ignored). You can open a demo state directly by URL: `/?s=<drawing index>&q=<question>`.

## How it works

```
any file ──► convert (recognised by content) ──► unified sheet ──► question engine ──► answer + evidence + honesty tier
```

| module | role |
|---|---|
| `drawqa/convert.py` | any PDF / PNG / JPG / DXF / IFC → one unified sheet (elements, texts, rooms, floors, scale) |
| `drawqa/pdfsheet.py` | vector PDF without any CAD file: text, printed scale (metric and imperial), room outlines, apartment outlines |
| `drawqa/detect.py` | trained element detector, runs locally (`models/detector.pt`); whole-sheet or tiles, four orientations |
| `drawqa/swiss.py`, `drawqa/ifc.py` | exact readers for DXF layers and BIM models |
| `drawqa/router.py` | question (EN/FI/DE/FR/IT/ET/DA templates) → intent |
| `drawqa/query.py`, `drawqa/solve.py` | deterministic counting and area sums; every answer gets a tier: read, measured, detected, estimated or cannot determine |
| `drawqa/eval.py` | scoreboard (counts exact, areas within 2%) |
| `tools/modal_train.py` | detector training on Modal from three labelled drawing styles |
| `demo/` | stdlib HTTP server and a single-page viewer |

```bash
.venv/bin/python -m drawqa.convert <file> out.json                           # any file → unified sheet
DRAWQA_LIVE=1   .venv/bin/python -m drawqa.eval --source cubicasa5k          # detector live on each scan
DRAWQA_SWISS=pdf .venv/bin/python -m drawqa.eval --source swiss_dwellings    # Swiss plans from the PDF alone
```

## Scoreboard (preview set, all languages)

| source | score | notes |
|---|---|---|
| swiss_dwellings, DXF | 2326/2358 = **98.6%** | exact CAD rules |
| swiss_dwellings, PDF only | 2298/2358 = **97.5%** | text and linework exact; symbols by the detector, doors and windows snapped to the PDF's own rectangles (rule developed on these same plans) |
| ifc_models | 1025/1049 = **97.7%** | misses need a model that is not in the preview |
| cubicasa5k (scans) | 3628/4270 = **85.0%** | trained detector, all 241 plans; was 27.9% with the 2019 model |

See `HANDOFF.md` for per-question numbers, training details, what did not work, and open issues.

Licences: each drawing keeps its own licence (see `junction-dataset-preview/data/*/LICENSE.md`). Credit drawings when you show them.
