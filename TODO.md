# TODO: state at 15:15 on 2026-11-14, for the next agent or teammate

Read this first, then `README.md` (rules, how it works, measurements) and `HANDOFF.md` (history; section 11 covers
walls, areas and free-form questions). Written at 14:31, updated at 15:15 (section "Changes 14:31-15:15" and tasks
1-4, 8). Re-check anything marked "check".

## Next (user, 15 Nov night)

- **Done: our trained models run on Modal.** App `drawqa-infer` (`tools/modal_infer.py`, L4 GPU + 8 CPUs, same code,
  weights and library versions; deploy with `.venv/bin/modal deploy tools/modal_infer.py`). Client `drawqa/remote.py`;
  `DRAWQA_INFER=modal` is the default, `local` runs here; falls back to local if Modal is unreachable. Same detections
  as local (60/60 boxes on plan 41, confidences within 0.001). UK school sheet: 81 s (detector 19.5, outlines 9.3,
  OCR 51.6) instead of 269 s on the Mac. OCR is now the slowest step (tiles in sequence on CPU): parallelise next.
  Redeploy after changing drawqa code or weights. The demo server warms it at start (and Kimi).
- New demo flow: `.venv/bin/python -u demo/server.py` -> http://localhost:8000/app (upload -> reading -> ask Kimi).
- Picture-first answering is in `drawqa/layer.py` (reading, once per file) + `drawqa/ask.py` (answers from the reading
  only; counts from the checked reading; other questions by an answer agent, Kimi on Modal now, Claude later via
  `drawqa/llm.py`). Qwen3-VL-32B was tried as the answer model: about 8-20x slower than Kimi and gave confidently
  wrong answers (61 m2 printed area answered as 77.56 "measured").

## Changes late evening (session A, junction-d0; user approved; not committed)

- **Counts are what people see on the drawing.** The dataset's own Swiss door/window answers count elements the DXF
  stores twice, exactly on top of each other (61 of 65 plans; 1,777 doors in the answers vs 1,501 drawn; site1000:
  36 vs 29). `drawqa/swiss.py` now counts such copies once (`sheet["duplicates_dropped"]` keeps how many); answers
  do not mention the hidden copies. This reverses the earlier choice in HANDOFF.md ("Swiss doors: the answer key
  counts CAD rectangles"): `snap_to_linework` in `drawqa/convert.py` now also counts a path drawn twice once, so the
  PDF route gives the visible count too (site1000: DXF, PDF and PNG all 29 doors / 26 windows).
- **Eval corrections:** `data/answer_corrections.jsonl` (150 rows, built by `tools/make_answer_corrections.py`)
  replaces those answers with the visible count; `drawqa/eval.py` applies it, keeps the original as
  `q["dataset_answer"]` and prints how many it replaced. Swiss DXF eval stays 2326/2358 = 98.6% (doors and windows
  130/130). This check is circular on the DXF route (same rule); the picture route (`DRAWQA_SWISS=pdf`) is the
  independent test: re-run it.
- **Legend symbols are not counted:** `drawqa/convert.py` `legend_boxes()` / `drop_legend_symbols()` find a
  Legend/Legende/Legenda heading in the sheet's text, take the ruled panel around it (or a small area by the heading)
  and drop detector symbols inside it; `drawqa/solve.py` adds "Not counted: N door symbol(s) drawn in the legend".
  Swiss site1000 picture: 29 doors (was 30). CubiCasa plan 1000 unchanged.
- **Detector on held-out plans, doors and windows:** CubiCasa 5 plans 104/104 (+1 extra); Swiss 6 plans 239/239
  (+1 = the legend sample, now dropped), scored with `tools/swiss_labels.py` boxes. Opus alone is worse (misses some,
  boxes doors on the swing), so Opus labels are not worth training the detector on for doors/windows.

## Changes 17:40-20:30 (session B; branch `freeform-tools`)

- **Doors from swing arcs in vector PDFs** (`drawqa/doorarcs.py`, `convert.add_door_arcs`): quarter-circle arcs of
  door-like radius at the sheet's scale, from Bezier curves, flattened polylines, or one-line-per-path exports.
  65 Swiss preview PDFs vs the CAD files' distinct door positions: exact on 35, within one on 59, 1,494 vs 1,498
  doors in total (developed on one plan). US A477: 12 doors, 0.81-0.91 m leaves. Cherokee Path: none found (arcs
  broken up). Every PDF door count reports it; it is the answer when the detector does not know the style.
- **Stoa's UK school sheet end to end** (14000 px image): convert 264 s, 1.9 GB peak (pre-convert for the demo;
  cached now); scale 1:200 from the bar; 128 doors, 67 windows (detector, one orientation: big sheets skip the
  four-orientation pass, `detect.TTA_MAX_TILES`, the other session; the note goes into the answer's basis).
- **Room names read by OCR on images** (`convert.ocr_room_names`): name lines grouped into blocks, kept when inside a
  room outline, attached like the vision reader's names. UK school: Group Room x10, Secondary Classroom x8 (incl.
  Severe Needs), Store x6 ... "How many secondary classrooms": 8, read. "Total area of the secondary classrooms":
  441.84 m2 over 8 rooms (~55 m2 each), detected. Outlines holding several names are flagged as merged rooms.
- Router / model guards: "total area of the <named rooms>" is not the whole floor; a step that would measure every
  room while the question names one is refused ("library" not found -> cannot determine, not 926 m); printed names
  that contain the question's room words beat a room type the model picked (it once took classrooms for bedrooms);
  read_value picks a label whose value has the asked kind (area vs count). Type counts say when no printed name
  means that type ("none of the 64 names means sauna").
- `DRAWQA_DETECT=0`: text-only conversion (OCR text, scale, tables; no detector or wall model), ~1 s per sheet with
  cached OCR. `tools/freeform_check.py piping|dorm|slab` uses it.
- Checks: question set 33 / 33 checkable answers right (Swiss 16, BIM 8, Stoa sheets 12); scoreboards unchanged
  (85.0 / 98.6 / 97.7).
- Detector fine-tune `f26l` (e26l + FloorPlanCAD) finished 19:35; **not used**. Held-out 0.8532 (e26l 0.8597);
  on Stoa's UK school it boxes almost no rooms (10 vs 54) and misses mirrored doors. Weights in `models/f26l.pt`.
- 21:15 (user approved): **detector switched to `e26l`**. `models/detector.pt` + thresholds are copies of e26l,
  `data/predictions/active.txt` = `yolo_e26l_tta.jsonl` (c26l kept as `models/c26l.pt`). UK school hand-checked
  wing: 23/23 doors found (c26l 16/23), 5 false on furniture. Scoreboards: scans 86.1% (was 85.0%), Swiss 98.6%,
  BIM 97.7%. Local only: the weights are not in git; the routing fixes went to `freeform-tools` as e993360.

## Changes 16:45-17:40 (session B; branch `freeform-tools`)

- **Gold set** (teammates' 86 judge-style questions; answers still unverified drafts), first full run with the model:
  60 / 83 right. Misses by cause: 9 US floor plans with printed room areas (fixed below), 7 table questions (other
  session fixed 5 in `schedule.py`, branch `schedules-e26l`), 5 FZK-Haus BIM questions (its model file is not in the
  local preview: refusing is correct here), 2 fixture counts on US restroom details (detector does not know US CAD
  symbols). After the fixes: about 74 / 83 (from partial re-runs, not one clean full run).
- **US-style room labels** (`tags.printed_room_areas`, `convert.add_printed_room_labels`): name over "218 SF", also on
  rotated sheets; unit totals "(762 SF)" next to unit tags become the units' areas; rooms go to the nearest unit tag.
  Replaces a vector PDF's rooms when its own reader found fewer named rooms with an area (on A477 it had mistaken
  door-type tags U41/U02 for rooms). Answers stay in the sheet's own SF unless the question asks for another unit.
  The 9 gold questions on Cherokee Path / A477: 9 / 9, tier read.
- Room types on named sheets: `convert.name_meaning` reads a name word by word ("1BD - BATH - OPTION" = bathroom);
  type areas and counts use it. `label_area` takes an optional unit; new tool `count_rooms`.
- Router: plain "how many bathrooms" = bathroom count; "<room> in unit U212" goes to the model (it passes the unit).
  Preview questions changed: 2 ifc_bench text questions, both closer to what they ask.
- Detector on Stoa's UK school sheet (c26l, tiles): about 17 of ~20 doors in a checked part, exterior windows
  reliable; misses are exterior doors below threshold, 1-2 false doors on desks. The piping plan is not a floor plan.
- In progress: FloorPlanCAD training data on Modal (subagent, `tools/modal_fpc.py`, writes `/data/yolo_fpc`); the
  other session will fine-tune from `e26l` (86.1% on preview scans) on CubiCasa + FloorPlanCAD once it is ready.
- Scoreboards unchanged: scans 85.0%, Swiss 98.6%, BIM 97.7%.

## Changes 15:15-16:45 (session B and its subagents; branch `freeform-tools`)

Stoa posted example question types and four example sheets (UK school plan 1:200, US dorm plan with an area list,
US foundation/slab plan, US pumphouse piping plan; files in the user's Downloads, 9934-14000 px wide PNGs). Test
files may be any format. Everything below works on the sheet's list of texts, whatever produced it.
- **`drawqa/textlayer.py`**: big images (over 5000 px) are OCR'd in native-resolution 2000 px tiles. Piping plan: 24
  -> 54 of ~56 equipment tags read, notes read cleanly; 23-68 s per sheet on the Mac, cached.
- **`drawqa/scalebar.py`** (subagent): scale from the scale bar measured on the image, the printed scale + paper size,
  and the dimension strings. On all four sheets the three agree within 0.1%. Wired in `convert.scale_from_text`
  (before outlines; replaces a missing, estimated, or >2%-different scale; its boxes are kept as evidence).
- **`drawqa/pdfsheet.py`**: `SCALE: 1/4" = 1'-0"` was read as 1:4 (12x wrong on every US vector PDF); now 1:48.
- **`drawqa/tags.py`** (subagent): tag inventory by prefix with gaps and the sheet's own definitions, pipe sizes,
  find-text, "label = value" lists. Piping plan: CV 5 (agrees with the sheet's "TYP. OF 5"), FSCV 7, FP 5, FSI 5,
  FSR 2, PIT 1-6 (PIT 4 not read), PS 41 labels / 23 type numbers (PS-2 gap); suction header 16". Dorm: all 20
  area/count pairs (West wing dormitory 6,095 SF, total second floor 26,562 SF).
- **`drawqa/slabs.py`** (subagent): slab callouts (thickness, bars, spacing, face, top of slab) and area per type.
  Foundation plan: 300/D16 217.8 m2, 150/D13 61.7 m2 (= 6200 x 9950), 300/D22 26.5 m2 (= 6200 x 4272, includes the
  isolated genset pad inside it). Tier "estimated": which region a callout covers is judged (position / leader).
- **New question tools** (`llm_router`, `query`): `count_tags`, `find_text` (numbered notes read whole: "note 4"),
  `read_value`, `pipe_size`, `slab_areas`; answers from text are tier "read" (OCR: "a character can be misread").
- **Room names on scans** (`convert.attach_printed_names`): names read by the vision model go to the room outline they
  sit in; label questions work on scans; type counts and areas say when printed names disagree ("names show 3
  bedrooms, the detector typed 1"); a fixed name dictionary (MH/MAKUUH = bedroom ...), never the model's guess.
- **Units and arithmetic** (`solve._finalize`): "in square feet", "in cm" converted by code with the factor shown;
  "minus 10", "+10% waste" passed by the model as `adjust` and applied by code. Router: cm/mm/sq ft are measurements;
  arithmetic questions go to the model; "Stanza" (= room) is not taken as "bedroom".
- Scoreboards after all of it: scans 85.0%, Swiss 98.6%, BIM 97.7% (unchanged).
- **Gold set** (teammates' 86 judge-style questions, all answers still unverified drafts): run in progress at 16:45
  (`scratchpad/goldrun/run.sh`, one drawing per process); 7 right / 12 wrong after 5 of 11 drawings, on code from
  before the latest fixes. **Next:** finish it, sort misses by cause, and get some gold answers verified by a person.
- **Not done:** the four example sheets end to end through `convert()` + `solve()` (only the text tools were run on
  their saved OCR text), door/window counts on CAD-style images (our detector does not know the style; a
  FloorPlanCAD-trained detector is the candidate), pipe lengths, SVG/DWG in `convert()`.

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
- **Storey plans of BIM models** (found by the user testing the demo, 15:20): on `..._plan-04-4-korrus.pdf`, "how many
  doors are there?" counted the whole building (211). `ifc.load` now sets `plan_storey` from the file name ("4.korrus",
  "Kælder"); a question naming no storey and no building / model / all floors counts that storey (42), and the model's
  context says which storey the plan shows. BIM 97.7% unchanged.
- **A 0 from a detector that does not know the style** (with the other session's `detector_fit` check: windows but
  no door gives `missing="door"`): a door count of 0 then, or any 0 when nothing at all is recognised, is "cannot
  determine" with the reason. Narrowed from "any 0 on a weak fit", which cost 32 right zeros on 5 weak preview scans
  ("0 saunas"); scans stay 85.0%.
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

With the sheet conversion stubbed out (no memory risk), latest run (after the 15:05 routing fix
`mentions_table_values` and the other session's `schedule.py` fixes: header words placed by column borders, multi-word
and "/" values matched, a value missing from the table gives None instead of 0, areas as numbers in sqft):
**26 of 64 right without the model, 51 of 64 with it** (was 22 / 43 at 14:55; the direct table run at 13:51 got 49).
The three A-701 fixture questions need the drawing itself.

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
