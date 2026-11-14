# TODO: state at 15:15 on 2026-11-14, for the next agent or teammate

Read this first, then `README.md` (rules, how it works, measurements) and `HANDOFF.md` (history; section 11 covers
walls, areas and free-form questions). Written at 14:31, updated at 15:15 (section "Changes 14:31-15:15" and tasks
1-4, 8). Re-check anything marked "check".

## Changes 14:31-15:15 (session B; on branch `freeform-tools`, not yet merged into `main`)

- **Final wall model installed** (`models/segmenter.pt` = `seg1` after 30 epochs; interim kept in `models/old/`).
  200 held-out plans, final vs interim: rooms outlined 87.3% vs 84.9%; room area error 1.9% vs 2.2% median (8.3% vs
  9.7% at p90); summed indoor area per plan 3.6% median, 16.8% p90; wall pixel IoU 0.77 vs 0.665. Live count answers
  on the first 10 preview scans: 0 of 186 changed by outlines (168 right either way).
- **Free-form questions with several steps work** (open model on Modal, warm: about 7-10 s per question; 20-25 s
  when the model lists seven storeys). Bugs found and fixed by testing:
  - The pattern router took the first thing named: "doors and windows" was a window count, "apartments A and B" was
    apartment A, "how many metres of skirting for the bedrooms" was a bedroom count, "wall area to paint in the kitchen"
    was the kitchen's floor area. These now go to the model (`router.py`); on all 1,770 distinct preview questions only
    two readings change, both previously wrong (A-701 "water closets, lavatories and showers"; ifc_bench "wall surfaces
    requiring painting").
  - On BIM models every model-read count ignored the storey ("doors on the second floor" gave 211 for the whole
    building instead of 42). Fixed in `llm_router.INTENTS`; also, on a BIM model a plain drawing pattern is re-read by
    the model when `llm=True`.
  - The open model's replies vary between runs: a single value in the list field, or several classes in one step,
    used to make the plan fail; `_validate` now repairs both (several classes are split and summed).
  - `storey = "*"` repeats one BIM step for every storey ("rooms on each storey"); `MAX_STEPS` 6 -> 8.
  - With `llm=True`, a pattern reading whose tool finds nothing gets one model reading.
  - A negative difference says in words that the first is smaller.
- **New tools** (`query.py`, `ifc.py`): `opening_width` (scans: along the wall; vector PDF: long side of the symbol
  at the printed scale, tier measured when the symbol matches the PDF's rectangles; BIM: OverallWidth),
  `room_perimeter`, `wall_area` (perimeter x the question's height; with no height, 2.5 m is assumed and the tier is
  "estimated", and the answer says so). Openings are not subtracted from wall area.
- **Table routing** (`solve.mentions_table_values`): a question that uses a table's cell codes (115.A, F4, W1,
  ESDH3066) or a column word plus a cell word ("area" + "locker") is a table question, so it no longer converts the
  42x30 sheet first. Skipped when the patterns read the question as a BIM query. On the 2,644 preview PDF questions
  exactly 14 change, all `opening_design`.
- **Check script:** `tools/freeform_check.py swiss|scan|ifc|all`: about 30 Stoa-style questions, 20 with known
  answers. Swiss (11) and BIM (8) passed in full; the two scan misses were fixed and re-checked one by one.
- **Scoreboards on this code:** Swiss 2,326 / 2,358 = 98.6%, BIM 1,025 / 1,049 = 97.7% (both unchanged); scans not
  re-run (no scan question's routing changed; last 85.0%).

## What this project is

Junction 2026, Stoa challenge: answer questions about one unknown construction drawing (PDF, PNG/JPG, DXF, IFC,
possibly SVG or DWG) and say honestly how each answer was obtained. Every file is converted to one unified sheet
(`drawqa/convert.py`); deterministic code counts and measures on it (`drawqa/query.py`, `ifc.py`, `schedule.py`);
every answer carries a tier: read, measured, detected, estimated, cannot determine (`drawqa/solve.py`).
A language model only chooses which tools to run; it never produces a number.

What Stoa said today (relayed from their Discord): real questions are more complex than the dataset's (their example:
"How many square meters of tiling should I order for the toilets"); they will test a few complex drawings through
whatever interface we provide; a few cents per drawing is fine, $10 is not; the dataset's answers were made by
humans. They will post example questions later today: **check Discord and try them**.

## What is running right now

| what | where | state at 14:31 | what to do |
|---|---|---|---|
| Wall + room-outline model, run `seg1` | Modal | **finished 14:48 and installed 14:51** (see Changes above) | nothing |
| Open language model (Qwen3-VL-32B) | Modal app `drawqa-reader`, deployed | up; scales to zero 10 minutes after the last call; a cold call blocks for minutes | warm it before a demo (task 2) |
| Detector `e26l` (d26l + sauna-room labels) | Modal, spawned 13:37 by the other session | **finished** (checked 14:40): held-out exact 0.8597 on 479 plans, best of the three (`d26l` 0.8572, `c26l` 0.8582). Not yet scored on the preview scans; weights not downloaded | task 5; owner is the other session |
| Local processes | this Mac | none heavy; swap 4.7 of 6.1 GB used | see memory rule below |

Nothing else is running. No watcher is waiting on `seg1`: someone has to look.

## Rules that bit us today

- **Memory.** This Mac has 8 GB. Swap hit 7.7 of 8 GB at about 14:20 (the 08:29 kernel panic was the same
  condition). Always pass `--source` to `python -m drawqa.eval`. Never run the 64 `opening_design` questions in
  one process: a question the table cannot answer falls through to converting a 42 x 30 inch sheet. Check
  `sysctl vm.swapusage` before any long local run, and run only one at a time.
- **Network drops.** A drop at 13:17 killed two Modal runs started with `modal run --detach` and a blocking
  `.remote()`. Long Modal work must be `.spawn()`ed (`--stage resume_seg`, `--stage pipeline` do this).
- **Modal stage names use underscores** (`train_seg`). A hyphenated name silently runs nothing.
- **macOS has no `timeout`**: use `perl -e 'alarm 20; exec @ARGV' <command>`.
- **Two agent sessions share this folder.** File split is in `README.md` section 2. Message the other session
  (`ListAgents`, `SendMessage`) before editing its files. `--stage convert*` rebuilds `/data/yolo` on the volume.
- **Test sets.** The 241 preview scans and 65 preview Swiss plans are never used for training or tuning.

## To do, in priority order

### 1. DONE 14:51: install the final wall model

Results in "Changes" above and `HANDOFF.md` section 11. The steps, for the record: `models/segmenter.pt` was an interim checkpoint (about epoch 10). When the app above is gone from
`modal app list` and `runs/seg1/summary.json` exists on the volume:

```bash
DRAWQA_FILL=0.15 DRAWQA_GPU=L40S .venv/bin/modal run tools/modal_train.py --stage seg_eval --run seg1 --limit 200
.venv/bin/modal run tools/modal_train.py --stage weights --run seg1        # -> models/seg1.pt
cp models/seg1.pt models/segmenter.pt && cp models/seg1.eval.json models/segmenter.eval.json
```

Then confirm on 20 preview scans that count answers are the same with `DRAWQA_OUTLINES=0` and `=1` (it was 0 of
240 changed with the interim model), and put the new held-out numbers in `HANDOFF.md` section 11. Interim scores
to beat (200 held-out plans): 85% of rooms outlined; room area off by 2.2% median after the size correction;
summed indoor area per plan off by 3.9% median, 19.7% for the worst tenth; wall pixel IoU 0.665.
If the run was cut off again: `DRAWQA_GPU=H100 .venv/bin/modal run --detach tools/modal_train.py --stage resume_seg --run seg1`.

### 2. MOSTLY DONE 15:10: free-form questions (see "Changes" above; text below is the 14:31 state)

Left: Stoa's example questions from Discord; the Claude API backend (no key on this Mac, so the demo uses the open
model); run `tools/freeform_check.py all` once in full.

`drawqa/llm_router.py` sends the question plus the names on the sheet to a model, which returns a short plan
(tool calls and how to combine them: single, sum, difference, largest, smallest, each); `solve.combine()` does the
arithmetic. With no `ANTHROPIC_API_KEY` it uses the open model on Modal.

- Tested and working (single step, open model): the tiling question gives 10.31 m2 on Swiss
  `site1102_bldg2242_floor10281.pdf` and 10.0 m2 on scan `high_quality_architectural-16.png`; "renovation cost"
  is refused; "combien de chambres" counts bedrooms.
- **Not tested: multi-step plans** ("which apartment is bigger, A or B?", "how much bigger", "doors and windows
  together"). My test stalled, probably on a cold Modal model, and was killed. Warm the model first
  (`.venv/bin/python -c "from drawqa import reader; print(reader.text('Reply with {}', 20))"`), then:

```bash
.venv/bin/python -c "
from drawqa.solve import solve
p='junction-dataset-preview/data/swiss_dwellings/site1102_bldg2242_floor10281.pdf'
for q in ['which apartment is bigger, A or B?','How much bigger is apartment A than B?','How many doors and windows are there altogether?']:
    r=solve(p,q,True,llm=True); print(q,'->',r['answer'],r['unit'],'|',r['honesty']['tier'],'|',r['explanation'][:200])"
```

- The Claude API backend (used when a key is set) has never run against the live API.
- Then try Stoa's example questions when they are posted, and add tools for what they need. Likely gaps: wall
  area (needs a ceiling height the plan does not show), perimeter of a room, width of a given door or window
  (the data is on the sheet: openings carry `width_m`, but no question tool reads it yet).

### 3. Table path through `solve()`: run by the other session at about 14:55

With the sheet conversion stubbed out (no memory risk): 22 of 64 right without the model, 43 of 64 with it (the
direct table run at 13:51: 49). Seven table questions that did not name a table were routed to the drawing first;
fixed at 15:05 (`mentions_table_values`, see "Changes"). **Check:** the other session's re-run on that fix, and its
`schedule.py` fix for areas returned as text ("29690 SF"). The three A-701 fixture questions need the drawing itself.

`drawqa/schedule.py` (other session) rebuilds tables from a PDF's words and ruled lines. `solve()` now goes to the
table first when the question names a table or says schedule / scheduled / listed, refuses to fall back to
counting drawn symbols for such a question, and refuses no-model counts that are ambiguous
(`_ambiguous_count`). Measured before the hook, no model: 15 of 18 guessed counts right, and the three wrong ones
are exactly the ambiguous kind the guard refuses. The other session measured 49 of 64 at 13:51 by calling
`schedule.plan()` + `schedule.run()` directly, with the Modal model writing the query (counts 31 of 39, text 10 of 16,
areas 8 of 9); that was not through `solve()`, and came before the 14:03 change to matching of shortened values.
**Not done:** a run of the 64 `opening_design` questions through `solve()`. Do it per sheet, in separate
processes, watching swap, with `llm=False` first and then `llm=True`.

### 4. DONE 15:05 for Swiss and BIM (unchanged); scans not re-run, see "Changes"

Last full pass was before the final edits (table guards, sheet cache 64 -> 4, multi-step solver). Scans were
re-run after them (85.0%); Swiss and BIM were not. One at a time:

```bash
.venv/bin/python -m drawqa.eval --source cubicasa5k --show-wrong 0        # expect 3,628 / 4,270 = 85.0%
.venv/bin/python -m drawqa.eval --source swiss_dwellings --show-wrong 0   # expect 2,326 / 2,358 = 98.6%
.venv/bin/python -m drawqa.eval --source ifc_models --show-wrong 0        # expect 1,025 / 1,049 = 97.7%
```

### 5. Decide on the new detector (other session's task)

Three candidates, all 25 classes except `c26l` (15):

| run | preview scans | held-out exact (479 plans) | Swiss PDF check |
|---|---|---|---|
| `c26l` (installed) | 85.0% | 0.8582 | 97.5% |
| `d26l` (`models/d26l.pt`) | 85.3% | 0.8572 | not done: the run was killed for memory |
| `e26l` (on the volume only) | not done | 0.8597 | not done |

Steps for `e26l`, then the same Swiss check for whichever scores best on scans:

```bash
DRAWQA_GPU=L40S .venv/bin/modal run tools/modal_train.py --stage predict --run e26l --tta   # -> data/predictions/
DRAWQA_PRED=data/predictions/yolo_e26l_tta.jsonl .venv/bin/python -m drawqa.eval --source cubicasa5k --show-wrong 0
DRAWQA_GPU=L40S .venv/bin/modal run tools/modal_train.py --stage weights --run e26l --tta   # -> models/e26l.pt
DRAWQA_WEIGHTS=models/e26l.pt DRAWQA_SWISS=pdf .venv/bin/python -m drawqa.eval --source swiss_dwellings --show-wrong 0
```

The Swiss check runs the detector live on every page; watch swap (`eval` has `--limit` but no offset, so "halves"
means a `--limit` run first to see the memory curve). `data/predictions/active.txt` pins the scan predictions to
`c26l`, so new prediction files change nothing until that line is edited. Installing means copying the weights and
thresholds to `models/detector.pt` + `.thresholds.json` and editing `active.txt`; tell the other session first.
Nothing in the wall/area code depends on which detector is installed.

### 6. More input types

- **SVG**: `convert()` still says "cannot be read". PyMuPDF opens SVG and `convert_to_pdf()` gives a one-page PDF
  with the same text and linework; write it to `data/cache/` and send it through the PDF branch.
- **DWG and generic DXF**: `drawqa/cad.py` (other session) turns them into a PDF page with a text layer.
  `dwg2dxf` (LibreDWG) is installed; two DWG sheets from the US sets were converted and drawn (Oakfire2 A2 in about
  10 s). The demo routes DWG and DXF with unknown layers through it (`demo/server.py`), but that path has **not been
  run end to end**, and `convert()` does not call it. To use it in `convert()`: `info = cad.to_pdf(path)` (raises
  `cad.CadError`), then the PDF branch on `info["pdf"]`, with `m_per_px = info["m_per_pt"] * 72 / dpi` when it is not None.

### 7. Rehearse the demo end to end

`PORT=8765 .venv/bin/python demo/server.py`. Open a scan, a Swiss PDF, an IFC model and a US schedule sheet; ask
the tiling question, a count, an area, a comparison, a table question. Things to look for: first open of an
image takes about 7 s longer now (OCR + wall model, then cached); a cold Modal model makes the first free-form
question wait minutes; walls and outlines only appear on images, not on vector PDFs.

### 8. Documentation

`HANDOFF.md` section 11 now covers the final wall model, the open-model backend, multi-step plans, the new tools and
the table routing (15:10). Still missing there: the low-confidence room rule (`segment.FILL`), bare numbers as
areas, the sheet cache change. `README.md` is the other session's file.

### 9. Open questions for the organisers or the user

- **Licences.** Submissions are MIT. Ultralytics is AGPL-3.0, CubiCasa5k is CC BY-NC-SA, the Raster2Seq weights we
  did not use are non-commercial. RapidOCR (Apache-2.0) and the Anthropic SDK (MIT) are fine. Unresolved.
- **Cost per drawing.** Not measured. The detector, wall model and OCR run locally for free; the Modal model costs
  GPU time while warm (A100-80GB) and one call per free-form question.

## Known weak points (say them, do not hide them)

- Windows on scans: exact count on 50% of plans (the model finds 88% of windows; annotators split or merge panes
  both ways). Every count now states its counting rule and the count under two other rules.
- Areas on scans: a room with no outline and no printed area is missing from a total; the answer says how many
  rooms came from which source. A printed area that no outline confirms is taken as read, so a misread digit
  goes into the sum. Totals were checked against a printed total on one scan only (139.5 m2, exact).
- Scale on scans is an estimate unless three or more printed room areas agree with their outlines (rare on the
  Finnish preview scans: 1 or 2 plans in 30).
- The wall model misses some large open-plan rooms and draws a few stray walls outside the building.
- Hand-drawn archive scans: the detector warns that the style is unfamiliar; nothing is scored there.

## Where things are

- Code: `drawqa/` (engine), `tools/modal_train.py` (training, all stages), `demo/` (web demo).
- Weights (git-ignored): `models/detector.pt` = `c26l`; `models/segmenter.pt` = final `seg1` (interim in `models/old/`); `models/d26l.pt`.
- Modal: profile `politovalexander15`, volume `drawqa-data` (`runs/<run>/`, `yolo/`, `yoloseg/`).
- Remote: `github.com/ts7832/Bob` (git remote name `bob`), branch `main` (last push 14:24). Session B's work from
  14:31-15:15 is on branch `freeform-tools`, made without switching the shared folder's branch (the folder stays on
  `main`, with those changes still uncommitted there). Merge it into `main` when both sessions agree.
- Caches: `data/cache/` (detections, outlines, OCR, reader results), safe to delete.
