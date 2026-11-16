# Bob: Junction 2026, Stoa challenge

Answers natural-language questions about construction drawings and BIM models, shows the evidence on the drawing, and
says for every answer how it was obtained. This file is the entry point for any person or agent picking the project
up: what exists, what is measured, what is running, and the plan. `HANDOFF.md` has the long-form detail.

State as of 2026-11-14, about 13:45 local time, with corrections at 14:40. Every number below was measured that day
unless it says otherwise. **What is running and what to do next: `TODO.md`** (newer than this file where they differ).

**Newer (15 Nov night): read `docs/SESSION_2026-11-15.md` first** (picture-first reading, answers from the reading by
Kimi, our models on Modal, the `/app` demo page) and **`docs/DATASET_ISSUES.md`** (where the dataset's answers are
wrong: Swiss door/window answers count copies a DXF stores twice, legend sample symbols, scales, and more).
**Trained models:** not in git; `python tools/get_models.py` downloads them from the GitHub release `v0.1-models`
(or leave them out: by default they run on Modal, app `drawqa-infer`).

## 1. What we are judged on

Stoa, the company behind the challenge (international: Europe and US), hands over **one unknown file** and asks question after
question. What they value most is **honesty**: say for every answer whether it was read or measured deterministically
or not (detected by a model, estimated from an assumption), and say "cannot determine" instead of guessing. There is
no known scored hidden test set; the dataset's questions are our scoreboard, not the judging.

Consequence for priorities: the files that matter are real drawing sets (vector PDFs, any European language or
English, metric or imperial, schedules), not only the Finnish apartment scans that make up most of the dataset.

## 2. Rules for anyone working here (read before touching anything)

1. **Several agent sessions work in this folder at the same time.** Find them with `ListAgents`, talk with
   `SendMessage`. Current split of files:
   - Session A (`junction-d0`, wrote this file): `demo/`, `drawqa/reader.py`, `drawqa/detect.py`, `drawqa/postprocess.py`,
     `drawqa/trace.py`, `tools/modal_reader.py`, `tools/eval_*.py`, the detector stages of `tools/modal_train.py`,
     `README.md`, `HANDOFF.md` sections 1-10.
   - Session B (`junction-f9`): `drawqa/convert.py`, `segment.py`, `ocr.py`, `llm_router.py`, `router.py`, `query.py`, `solve.py`, `ifc.py`, `pdfsheet.py`, `textlayer.py`, `tags.py`, `scalebar.py`, `slabs.py`,
     the `*_seg` stages of `tools/modal_train.py`, `HANDOFF.md` section 11.
   Do not edit the other side's files without messaging first. Commit only your own files, by name (`git add -A`
   sweeps in someone else's half-finished work).
2. **The 241 preview scans and 65 preview Swiss plans are test sets.** Never train or tune on them.
3. **This Mac has 8 GB of RAM.** Always pass `--source` to `python -m drawqa.eval`. A run with no source ended in a
   kernel panic at 08:29 (memory and swap exhausted; the exact cause was not isolated).
4. **The network here drops.** At 13:17 a drop cancelled two Modal training runs that had been started with
   `modal run --detach` and a blocking `.remote()` call. Start long Modal work with `.spawn()` (the `pipeline` stage
   and `DRAWQA_SPAWN=1 --stage tune` do) and read results from the volume.
5. **`--stage convert`, `convert_swiss`, `convert_ifc` delete and rebuild `/data/yolo` on the Modal volume.** Ask the
   other session before running them. The segmentation data lives separately in `/data/yoloseg`.
6. **No Anthropic API key on this machine.** `drawqa/llm_router.py` (free-form questions) falls back to the open
   model on Modal (`reader.text`); single-step questions work that way, the Claude API path has never run live.
7. Check `df -h` before installing anything; do not download the 10 GB dataset to the Mac (it is on the Modal volume).

## 3. How it works

```
any file ─► convert (recognised by content) ─► unified sheet ─► question engine ─► answer + evidence + honesty tier
   vector PDF : text layer, printed scale, room and apartment outlines (exact) + detector on the rendered page,
                doors/windows snapped to the PDF's own rectangles
   image/scan : element detector (symbols, room types, floors) + segmentation model (walls, room outlines)
                + printed text (OCR, or the vision-model reader) + unfamiliar-style check
   DXF        : layer rules (Swiss family), exact
   IFC        : model query, exact
```

Unified sheet (`drawqa-sheet/1`, pixel coordinates of the page image, y down): `scale`, `elements` (doors, windows,
fixtures), `texts`, `rooms` (type, printed name, outline or box, area and where the area came from), `floors`,
`walls`, plus notes on how each part was obtained (`detector_fit`, `linework_notes`, `outline_notes`).

Honesty tier on every answer (`drawqa/solve.py`): **read** (printed on the sheet), **measured** (exact geometry or
model data), **detected** (a trained model found it; each item is drawn, confidence reported), **estimated**
(depends on a stated assumption), **cannot determine** (with the reason).

| Module | Role |
|---|---|
| `drawqa/convert.py` | any PDF / PNG / JPG / DXF / IFC to the unified sheet; linework snapping; scale from printed areas |
| `drawqa/pdfsheet.py` | vector PDF without CAD: text, metric and imperial scales, room and apartment outlines |
| `drawqa/detect.py`, `postprocess.py` | element detector (local, `models/detector.pt`), thresholds, `detector_fit` (unfamiliar-style check) |
| `drawqa/segment.py` | walls and room outlines on images (`models/segmenter.pt`) |
| `drawqa/ocr.py` | printed text on images via RapidOCR (optional) |
| `drawqa/reader.py` + `tools/modal_reader.py` | vision-language model on Modal that reads room names, printed areas and what each name means, in any language |
| `drawqa/swiss.py`, `dxf.py`, `ifc.py` | exact readers for DXF layers and BIM models |
| `drawqa/router.py`, `llm_router.py` | question to intent: patterns for the dataset's templates; language model for free wording (untested live) |
| `drawqa/query.py`, `solve.py` | deterministic counting, area sums, honesty tier |
| `drawqa/eval.py`, `tools/eval_heldout.py`, `tools/eval_trace.py` | scoreboard; Swiss PDFs outside preview and training; tracing experiment |
| `tools/modal_train.py` | Modal: dataset building, detector and segmentation training, tuning, prediction, weights |
| `demo/` | stdlib HTTP server and single-page viewer (upload, overlay, self-check, questions) |

## 4. What is measured

Scoreboard on the preview set (count questions exact, areas within 2%):

| Source | Score | Notes |
|---|---|---|
| Swiss plans, DXF | 2,326 / 2,358 = 98.6% | exact CAD rules |
| Swiss plans, PDF only | 2,298 / 2,358 = 97.5% | text and outlines exact; doors/windows from detector + snapping |
| Swiss plans, PDF only, 24 plans from sites never used | 798 / 824 = 96.8% | `tools/eval_heldout.py`; the independent check of the row above |
| BIM models (IFC) | 1,025 / 1,049 = 97.7% | misses need a model that is not in the preview |
| Scanned plans (CubiCasa) | 3,628 / 4,270 = 85.0% | detector `c26l`; floors, saunas, WCs, bathrooms 93-98%, doors 75%, windows 50% |
| Real US drawing sets (`opening_design`) | 51 of 64 with the model, 28 without; gold set (drafts) on US sets 51 of 70 | through `solve()` (15:35); tables rebuilt by `drawqa/schedule.py`, the Modal model writes the query, code runs it. 3 more are right but worded differently from the key; 3 need the drawing (fixtures in an enlarged plan) and are not answered |
| BIM free-text questions (`ifc_bench`) | 0 of 249 attempted | |

Other measurements that shape the design:

- **Open vision models cannot count symbols.** Qwen3-VL 32B finds about half the doors. They read text well.
- **The detector does not transfer across drawing styles by itself.** Trained on scans only it found 5% of Swiss
  doors; with Swiss plans in training, 94 of 96 sinks on unseen Swiss buildings.
- **Unfamiliar-style check** (`detector_fit`): flags 21 of 24 archive scans from four collections, 4 of 241 preview
  scans, 0 of 65 Swiss PDFs. The 3 archive scans it lets through were read plausibly (one is a US plan).
- **Vision-model reader** (Qwen3-VL-32B, `drawqa-reader` on Modal): Finnish hand-lettered plan, 15 of 15 names and
  printed areas; Italian Swiss plan 19 of 19 names; German 20 of 21; an English US archive plan, every name visible;
  a dense 10-apartment sheet only 50 of 85 (needs tiling). Its guess at what a name *means* was right for full words
  in every language tried and wrong for about 1 in 6 Finnish abbreviations (VH, TK, PESUH), so nothing is counted
  from the meaning. It is not stable across prompts: one prompt change lost two rooms on a rotated plan.
- **Self-check.** Names read by the reader versus rooms the detector typed: on three plans it listed 19 missing
  rooms, all truly missing, of about 26. Asking the model which of our tags are wrong produced only noise.
- **Room outlines.** Flood-fill tracing (`drawqa/trace.py`) did worse than plain rectangles (0.90 against 0.95 mean
  overlap with true outlines, 12 held-out plans) and is not used. The segmentation model is the route (HANDOFF 11).
- **Label gaps, not model size, caused the visible misses.** CubiCasa names general rooms `Room` ("H") and most
  saunas `Undefined`; the first training script dropped both.

## 5. Models

| Model | What | State |
|---|---|---|
| `c26l` | YOLO26-l detector, 15 classes, three drawing styles | replaced by `e26l` on 14 Nov 21:15 (kept as `models/c26l.pt`) |
| `d26l` | `c26l` fine-tuned with 10 more room types and style-varied copies | cut at epoch 18 of 30 by the network drop; held-out 85.7% against 85.8% on the scored types, new room types counted exactly on 85-98% of held-out plans; preview scans 85.3% (against 85.0%); Swiss PDF check and install decision pending |
| `e26l` | `d26l` + sauna-room labels, 12 epochs | **in use** (`models/detector.pt`, release `v0.1-models`, also on Modal `drawqa-infer`); held-out 86.0%; doors+windows 104/104 on 5 held-out CubiCasa plans, 239/239 on 6 Swiss plans |
| `seg1` | YOLO26-m-seg, walls and room outlines | **in use**, final (`models/segmenter.pt`). 200 held-out plans: 87.3% of rooms outlined, room area error 1.9% median, plan indoor area error 3.6% median (16.8% worst tenth), wall IoU 0.77 |
| Qwen3-VL-32B | room-name reader, not trained by us | deployed as `drawqa-reader`; cold start about 11 minutes, 13-35 s per drawing when warm, stays up 10 minutes after a call |

Earlier runs (`a11m`, `a26m`, `s11m`, `b11m`, `artd`) and what each showed: HANDOFF section 4. Modal spend so far
about $43 at 13:20, covered by credits (`modal billing summary`).

## 6. Run

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install ifcopenshell pymupdf modal polars && .venv/bin/pip install --no-deps ultralytics==8.4.212 ultralytics-thop   # once
PORT=8765 .venv/bin/python demo/server.py                                   # demo -> http://localhost:8765
.venv/bin/python -m drawqa.convert <any file> out.json                      # unified sheet
.venv/bin/python -m drawqa.eval --source cubicasa5k                         # scoreboard, ALWAYS one source at a time
.venv/bin/python -m drawqa.eval --source swiss_dwellings                    # (also: ifc_models)
DRAWQA_SWISS=pdf .venv/bin/python -m drawqa.eval --source swiss_dwellings   # Swiss plans from the PDF alone
DRAWQA_LIVE=1 .venv/bin/python -m drawqa.eval --source cubicasa5k           # detector live on each scan (about 16 min)
.venv/bin/python tools/eval_heldout.py                                      # Swiss PDFs outside preview and training
.venv/bin/python -m drawqa.reader <image>                                   # read room names (Modal GPU)
DRAWQA_GPU=A100-80GB .venv/bin/modal deploy tools/modal_reader.py::APP      # (re)deploy the reader
DRAWQA_GPU=H100 .venv/bin/modal run --detach tools/modal_train.py --stage pipeline --run NAME --model /data/runs/d26l/weights/best.pt --epochs 12
DRAWQA_SPAWN=1 DRAWQA_GPU=L40S .venv/bin/modal run --detach tools/modal_train.py --stage tune --run NAME --tta --data all
DRAWQA_GPU=L40S .venv/bin/modal run tools/modal_train.py --stage predict --run NAME --tta    # -> data/predictions/
DRAWQA_GPU=L40S .venv/bin/modal run tools/modal_train.py --stage weights --run NAME --tta    # -> models/NAME.pt
```

The dataset preview is expected in `junction-dataset-preview/` (git-ignored). Demo URLs: `/?s=<sample index>`,
`/?file=<full path>`, add `&q=<question>`. In the demo, dropping an image shows everything found on it; "Read room
names and areas" calls the reader and then reports which named rooms the detector did not mark.

Switches: `DRAWQA_OUTLINES=0` (no segmentation or OCR), `DRAWQA_SNAP=0` (no linework snapping), `DRAWQA_PRED=<file>`
(another predictions file for the scan scoreboard), `DRAWQA_WEIGHTS=<file>` (another detector), `DRAWQA_READER=1`
(call the reader during conversion instead of only using its cache).

## 7. Plan, in priority order

The aim is generality for an international firm: any language, metric or imperial, real drawing sets.

1. **Real US drawing sets: schedules.** In progress (Session A). The door schedule on sheet A-801 is recoverable from
   the PDF's own text once the page's 90-degree rotation is undone: 38 rows, matching the dataset's answer of 38.
   PyMuPDF's `find_tables()` returns nothing on these sheets, so the table is rebuilt from word positions and the
   sheet's ruled lines. Next: a general table extractor (`drawqa/schedule.py`), then questions over tables (count
   rows where a column has a value, look up a cell). Deterministic, tier "read".
2. **Printed names as the language-independent source of what a room is.** The reader's names and areas are already
   used for areas (Session B). Still to do: put names into `sheet["rooms"]` for rooms the detector did not type; tile
   large sheets before reading (dense sheets lose 40% of names); keep the model's "meaning" as a labelled guess.
3. **Imperial units end to end.** The PDF reader recognises imperial scales; lengths and areas in feet, inches and
   square feet in answers are untested.
4. **Detector follow-ups, in the background.** Score `d26l` on the preview and install it if it is not below 85.0%
   (tell Session B before switching `models/detector.pt`); then the same for `e26l`.
5. **Free-form questions with a live model.** Needs an API key, or a text call to the model already on Modal.
6. **Rehearse the judging**: one file of each type through the demo, with the reader warmed up beforehand.

## 8. Open issues

- **Licences.** Junction says submissions are MIT. Ultralytics is AGPL-3.0 and CubiCasa5k is CC BY-NC-SA. Unresolved;
  ask the organizers.
- **DWG** files: `drawqa/cad.py` converts them (LibreDWG `dwg2dxf`, installed) to a PDF page; the demo routes them
  there, but that path has not been run end to end and `convert()` does not use it.
- **Symbols on a drawing style nobody trained on** are not solved; the unfamiliar-style warning reports it.
- **Windows on scans (50%)**: one long window and several panes are labelled inconsistently in the training data.
- **Reader cold start (11 minutes)** and its GPU cost; warm it before a demo.
- **Dataset examples in the demo list**: the sample titled "French, large" carries Italian labels.
