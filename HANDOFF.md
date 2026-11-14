# HANDOFF: Junction 2026 / Stoa challenge (state as of 2026-11-14, early morning)

For the next agent or teammate. Every number here was measured in the session that wrote this file; the commands that
produce them are given. The previous version of this file had several wrong assumptions; section 8 lists them.

---

## 1. What we are judged on

- **Told by Stoa directly (2026-11-14):** they hand over **one unknown file** and ask question after question. What they
  value most is **honesty and transparency**: for every answer, say whether it was obtained deterministically (read or
  measured with the drawing's own scale) or not (for example a scale estimated from a standard object).
- There is **no known scored hidden test set.** `HACKATHON.md` and the dataset README only give scoring rules for the
  released questions. The questions are our scoreboard, not the judging.
- The file's type is unknown. The dataset README lists PDF, DWG, DXF, IFC, SVG, PNG and JPEG.

## 2. The design in one picture

```
any file ──► convert (recognised by content) ──► UNIFIED SHEET ──► question engine ──► answer + evidence + honesty tier
              PDF  : text layer, printed scale, room outlines, apartment outlines   (exact)
                     + rendered page ─► element detector                            (detected)
              PNG/JPG : element detector (symbols, rooms, floors)                   (detected)
              DXF  : layer rules (Swiss family)                                     (exact)
              IFC  : model query                                                    (exact)
```

**Unified sheet** (`drawqa/convert.py`, format `drawqa-sheet/1`; pixel coordinates of the page image, y down):

| field | content |
|---|---|
| `source`, `kind`, `page`, `width_px`, `height_px` | what was read |
| `scale` | `m_per_px`, `denominator`, `source` (printed text, several candidates, or "estimated from doors") |
| `elements` | `{id, type, bbox, conf, found_by}`: door, window, wc, sink, bathtub, shower, sauna stove/bench |
| `texts` | `{id, text, bbox}`: everything printed on the sheet (PDF text layer) |
| `rooms` | `{id, number, name, polygon or bbox, area_m2, area_source}` |
| `floors` | one box per storey drawing on the sheet |

**Honesty tier on every answer** (`drawqa/solve.py: honesty`):

| tier | meaning |
|---|---|
| read | the value is printed on the sheet |
| measured | exact geometry or model data, with the scale source named |
| detected | a trained detector found the elements; each one is drawn; confidence reported |
| estimated | depends on a stated assumption (e.g. scale from door width) |
| cannot determine | says why |

## 3. Results (all measured; preview questions, all languages)

| what | how | score |
|---|---|---|
| Swiss plans, DXF rules | `python -m drawqa.eval --source swiss_dwellings` | 2,326 / 2,358 = 98.6% |
| BIM models | `python -m drawqa.eval --source ifc_models` | 1,025 / 1,049 = 97.7% |
| Swiss plans, **PDF only, no DXF**, detector trained on scans only | `DRAWQA_SWISS=pdf python -m drawqa.eval --source swiss_dwellings` | 1,976 / 2,358 = 83.8% |
| Scanned plans, old 2019 model (31 plans) | | 166 / 594 = 27.9% |
| Scanned plans, always answer the most common value | | 55.9% (English) |
| Scanned plans, trained detector `a26m`, all 241 plans | `python -m drawqa.eval --source cubicasa5k` | 3,622 / 4,270 = 84.8% |
| Same, live on this Mac from the image alone | `DRAWQA_LIVE=1 python -m drawqa.eval --source cubicasa5k` | 3,622 / 4,270 = 84.8% (3.4 s per plan) |

Scanned plans per question type (`a26m`, four orientations):

| question | correct | | question | correct |
|---|---|---|---|---|
| floors | 97.5% | | sinks | 85.4% |
| saunas | 97.0% | | showers + bathtubs | 84.1% |
| balconies | 97.1% | | kitchens | 85.7% |
| WC rooms | 95.5% | | bedrooms | 86.4% |
| WC seats | 94.6% | | doors | 69.9% |
| bathrooms | 94.6% | | windows | 50.6% |

Swiss, PDF only (the split that matters for generalisation):

| part | from | correct |
|---|---|---|
| room-label counts, apartments, label areas | PDF text and dashed outlines | 100% |
| room areas | printed value, else outline geometry at the printed scale | 258 / 260 |
| apartment and floor areas | same | 210 / 224 and 122 / 130 (same misses as the DXF route) |
| bathtubs, WCs, washbasins | detector that never saw this style | 75%, 79%, 63% |
| windows, doors, showers | detector that never saw this style | 25%, 5%, 24% |

**Why windows and doors are hard on scans** (diagnosed on 383 held-out plans, `--stage diagnose`): the detector finds
88% of windows and 94% of doors. Most window errors are one long window drawn by the annotator as one box and split
by the model into panes, or the reverse. A merge rule tuned on held-out plans did not help because the labels go both
ways. With about 8 windows per plan, 90% per window gives roughly 50% exact counts.

## 4. Training (`tools/modal_train.py`, Modal volume `drawqa-data`)

**Labels are the dataset's own answer key.** The scanned-plan answers are CubiCasa5k's SVG annotation counts. With
the rules below, label counts reproduce 40,557 of 40,622 English answers on 4,759 plans (99.84%):

- door = `Door` not of class `Door None` (open doorway); window = `Window`
- shower = `Shower` + `ShowerPlatform` (not `ShowerCab`, not `ShowerScreen`); bathtub = `Bathtub*`; sink = any `*Sink*`
- bathroom = `Space Bath*` whose label is not WC; WC room = `Space Bath*` labelled WC
- floor = one box per `Floorplan` group; sauna = stove, or benches, or a sauna room
- images: CubiCasa `F1_original.png` (pixel-identical to the Junction PNG); SVG coordinates are in `F1_scaled.png` space

**Three drawing styles with exact labels:**

| source | plans | labels from | classes |
|---|---|---|---|
| CubiCasa scans | 4,759 (241 preview plans excluded) | SVG annotations | all 15 |
| Swiss plans | 971 (65 preview plans excluded, split by site) | DXF geometry mapped to the PNG (`tools/swiss_labels.py`) | 6 symbols |
| BIM plan exports | 71 | element classes in the SVG | door, window |

**Runs so far** (50 epochs, 1280 px, A100-40GB unless noted):

| run | model | data | held-out exact counts | preview scans |
|---|---|---|---|---|
| a11m | YOLO11-m | scans | 85.3% (4 orientations) | 83.7% |
| a26m | YOLO26-m | scans | 87.1% (4 orientations) | **84.8%** |
| a11m+a26m | both fused | scans | 86.6% | 85.0% |
| s11m | YOLO11-m, symbols only | scans | same symbol counts as a11m (±2 plans; windows +7) | n/a |
| artd | RT-DETR-l | scans | loss went NaN at epoch 5 (mixed precision); stopped | n/a |
| b11m | YOLO11-m | all three styles | scans 84.3%; unseen Swiss buildings: sinks 91/96, WCs 92/96, showers+bathtubs 89/96, windows 68/96 | 83.4% |
| c26l | YOLO26-l, H100, 60 epochs | all three styles | see section 9 | |

**What we learned:**
- YOLO26 beats YOLO11 slightly; fusing the two adds nothing worth the cost.
- Learning rooms in the same detector costs no symbol accuracy (s11m vs a11m).
- Adding the Swiss and BIM styles to training costs nothing on scans (b11m 83.4% vs a11m 83.7% on the preview).
- Four-orientation detection with fusion adds about 1 point.
- **Scale matters:** large scans must be read whole (fitted to 1280 px), as in training; cutting them into tiles lost 3
  points. Big multi-apartment sheets are the opposite. The rule in `_detect_one` / `drawqa/detect.py`: read the whole
  sheet first; cut into native-resolution tiles only if its doors come out smaller than 30 px or none are found.

**Swiss doors: the answer key counts CAD rectangles, not door symbols.** In the 65 preview Swiss plans the key counts
1,777 door rectangles at only 1,501 distinct positions (two overlapping rectangles for one door on 61 of 65 plans). A
detector that correctly marks every visible door therefore disagrees with the key on almost every Swiss sheet (held-out
check: every true door had a predicted box, yet the count matched on 4 of 96 plans). The DXF route reproduces the key
exactly; from the PDF the detector reports the number of visible doors. We did not teach the detector to imitate this.

**What does not work (measured):**
- Open vision models as symbol finders: Qwen3-VL 8B and 32B find about half the doors and a third of the windows
  (`tools/modal_vlm_test.py`). The 32B model reads rooms and labels well (33 / 35).
- Plain OCR (tesseract, English) for room labels on scans: 14 / 28.

## 5. Files

```
drawqa/convert.py      any file -> unified sheet (content sniffing; scale estimate from doors with measured error)
drawqa/pdfsheet.py     vector PDF without CAD: text, metric + imperial scales, room outlines, dashed apartment outlines
drawqa/detect.py       trained detector locally: whole-sheet / tile rule, four-orientation fusion, thresholds
drawqa/postprocess.py  thresholds + merge rules shared by training-time tuning and the engine
drawqa/scan.py         scanned plans from a predictions file (data/predictions/active.txt names the default)
drawqa/swiss.py, dxf.py, geom.py   Swiss DXF rules (window boxes fixed: one 4-point rectangle per window)
drawqa/ifc.py          BIM queries
drawqa/router.py       question -> intent (regex over the dataset's templates; free-form questions NOT handled)
drawqa/query.py        deterministic counting and area sums
drawqa/solve.py        solve(path, question) -> answer, evidence, honesty tier
drawqa/eval.py         scoreboard
tools/modal_train.py   stages: download, convert, fetch_junction, convert_swiss, convert_ifc, train, tune, predict,
                       diagnose, weights, smoke
tools/swiss_labels.py  Swiss DXF -> boxes in PNG pixels (registered by room outlines, not by text)
tools/modal_vlm_test.py  open vision model tests (count / mark modes)
models/detector.pt (+ .thresholds.json)   weights used by the live path (git-ignored)
data/predictions/      per-run predictions and thresholds for the 241 preview scans
```

## 6. How to run

```bash
.venv/bin/python -m drawqa.convert <any file> out.json     # unified sheet
.venv/bin/python -m drawqa.eval [--source X]                # scoreboard
DRAWQA_LIVE=1  .venv/bin/python -m drawqa.eval --source cubicasa5k       # detector live on each image
DRAWQA_SWISS=pdf .venv/bin/python -m drawqa.eval --source swiss_dwellings  # PDF only, no DXF
PORT=8765 .venv/bin/python demo/server.py                   # demo (port 8000 belongs to another program)

DRAWQA_GPU=A100-40GB .venv/bin/modal run --detach tools/modal_train.py --stage train --run NAME --model yolo26m.pt --data all
DRAWQA_GPU=L40S .venv/bin/modal run tools/modal_train.py --stage tune    --run NAME --tta --data all
DRAWQA_GPU=L40S .venv/bin/modal run tools/modal_train.py --stage predict --run NAME --tta
DRAWQA_GPU=L40S .venv/bin/modal run tools/modal_train.py --stage weights --run NAME --tta   # -> models/detector.pt
.venv/bin/modal billing summary                              # real spend
```

Modal notes: GPUs needed a payment method on the account (added). The volume is a slow network disk, so `train` copies
its dataset to local disk with 64 threads first. `--detach` keeps a run alive if the laptop disconnects.

## 7. Open issues

1. **Licences.** Junction says submissions are open-sourced under MIT. Ultralytics is AGPL-3.0 and CubiCasa5k is
   CC BY-NC-SA. Ask the organizers whether dependencies and model weights fall under the MIT rule.
2. **Free-form questions.** The router only understands the dataset's question templates. A judge's own wording needs
   an LLM to choose the tool. There is still no API key on this machine.
3. **DWG** files cannot be read (no converter installed).
4. **Scans have no area method.** The detector gives room boxes, not outlines; the dataset asks no area questions on
   scans, but a judge might. Scale on a scan can only be estimated (door width: about 5% typical error, up to 25%,
   measured on 1,777 Swiss doors).
5. **Hand-drawn archive scans** (374 files, no questions, no labels) are untested.
6. **Schedules and tables** on real drawing sets (`opening_design`, 64 questions) are not handled.

## 8. Corrections to the previous handoff

- "Hidden test": an assumption. See section 1.
- "27.9% on scans": below a constant guess (55.9%); the old model returned nothing for 6 of 12 question types.
- Full dataset share: scans are 67% of questions, BIM 2.4% (preview: 53% and 13%).
- The training script had four faults (dropped sauna stoves, counted shower cabins, trained on the wrong image
  version, no room or floor classes). Rewritten.
- "Unified sheet graph with views, texts, scale and metric flag" was described but not in the code. It is now, as in
  section 2 (no views yet).

## 9. Latest runs

(filled in at the end of the session; see the table in section 4 for the meaning of each run)

## 10. Dataset reference (carried over from the first handoff, re-checked where marked)

`junction-dataset-preview/` (git-ignored, 0.7 GB): `questions.jsonl` has 7,990 rows `{path, question, answer, unit, type}`;
scoring: counts exact, areas/lengths/volumes within 2%, text free-form. Most questions appear twice (English + a local
language) with identical answers. Files with the same name and another extension are the same drawing.

| source | preview files | preview questions | full-set questions (re-checked) | what it is |
|---|---|---|---|---|
| cubicasa5k | 241 PNG | 4,270 | 85,514 on 4,787 plans | scanned Finnish apartment plans (CubiCasa5k), any orientation |
| swiss_dwellings | 65 × (PDF+DXF+PNG) | 2,358 | 37,584 on 1,040 plans | rendered from Swiss Dwellings geometry; labels in DE/FR/IT |
| ifc_models | 4 IFC + 3 plan PDF/SVG | 1,049 | 3,062 | BIM models and plan exports (SVG carries `data-guid` and `class="IfcDoor"`) |
| ifc_bench | 5 IFC | 249 | 820 | IFC-Bench v2 free-text questions (not attempted) |
| opening_design | 55 PDF, 20 DWG, 5 IFC, 1 DXF | 64 | 64 | real US drawing sets; schedule questions (not attempted) |
| habs, finna, europeana, wikimedia | 374 JPG/SVG | 0 | 0 | archive scans, unscored |

Swiss DXF rules (98.6%): door = closed 5-point polyline on layer DOOR; window = one 4-point rectangle on WINDOW (plus two
glass lines); fixture = rectangle on FIXTURE classified by inner shapes (ellipse + line = WC, ellipse = washbasin,
>60-point outline = bathtub, empty near-square 0.65–1.05 m = shower); room = number text + name before + "xx.xx m²"
after, else the area of the smallest ROOM_OUTLINE containing the number; apartment = UNIT_OUTLINE containing an
"App. X" / "Whg. X" label. Misses: showers drawn in another style (28/42) and which spaces count in area totals.

BIM (97.7%): quantity sets, else the 3D footprint; wall lengths are in mm (use `calculate_unit_scale`); count only
top-level stairs. All 24 misses need the FZK-Haus model, which is in the full set but not the preview.

Unverified notes from the first handoff: about 35 of the 374 archive scans are not measurable drawings (photos, sheet
music, maps, renders); `ifc_models/resbim-388_model.ifc` is essentially empty.
