# Frontier model + our detector: what was tested (2026-11-14)

Question: can a frontier vision model (Claude, run as a Claude Code agent, no API key) rebuild a floor plan in
DrawQA's sheet format, check it against the image in a loop, and let our unchanged engine answer from it?

How: `plan_tool.py` gives the model a pixel grid, an overlay of its own model and zoomed check tiles;
`engine_on_model.py` turns the model's JSON into a `drawqa-sheet/1` through `convert.add_elements` (the same path the
detector's output takes) and runs `solve()` on every dataset question for that plan. Answer keys were kept outside
the agents' folders. Round trip check: the detector's sheet converted to the model format and back scores exactly the
same as the engine (71/95 on 10 plans), so differences come from the model.

| Setup (preview scans) | Questions | Right |
|---|---|---|
| Detector's sheet (our engine as it is) | 195 | 159 (81.5%) |
| Claude from scratch, 6 settings on plan 6001 | 9 each | 5-7 (detector 7) |
| Detector, then Claude (Opus, low effort) may edit everything | 95 | 68 (detector 71) |
| Detector, then Claude constrained (no adding symbols) | 100 | 90 (detector 88) |
| **Claude's rooms + detector's doors, windows, fixtures** | 195 | **167 (85.6%)**: 10 gained, 2 lost |
| same + fixtures checked against Claude's room types | 195 | 167 (2 fixtures dropped, no answer changed) |

What we learned:
- Claude reads printed room names, types rooms and reads scales from dimension lines well; the detector leaves many
  rooms untyped. All gains are room-type questions.
- Claude adding doors, WCs or sinks hurt: it counts partly drawn neighbouring units and does not know the dataset's
  conventions (window panes, folding doors). Merging windows cost answers (the key counts panes).
- Effort: low effort was as accurate as medium and 2.5x faster; default effort took 12-14 min per plan for no gain.
- Cost per small plan: about 50 s and 55k tokens (Opus, low effort); the detector takes about 3 s.

## Room layer against CubiCasa's own room polygons

`room_eval.py` scores a room layer against the annotation behind the dataset (CubiCasa `model.svg`, 20 preview plans):
a room is found at IoU >= 0.5; extra = a model room on no annotated space; types on matched rooms. Spaces the annotation
leaves untyped ("Undefined", a yard) are optional. The brief was tuned on 10 plans (dev) and then run once, unchanged,
on 10 others (held-out). Start: the wall model's outlines (unnamed) + detector symbols + OCR words; the model names,
types, splits, merges, adds and deletes, using `plan_tool.py lint` (overlaps, two names in one outline, a name in no
outline, unnamed rooms without a door).

| Room layer (dev, 87 typed rooms) | found | extra | mean IoU | types right |
|---|---|---|---|---|
| detector room boxes (old start) | 38 | 0 | .411 | 38/38 |
| wall-model outlines alone | 80 | 11 | .856 | 35/81 |
| Opus, constrained review of the detector sheet | 83 | 3 | .872 | 78/84 |
| Sonnet low, rooms drawn from scratch | 73 | 4 | .754 | 72/75 |
| Haiku low, rooms drawn from scratch | 81 | 10 | .827 | 79/84 |
| Sonnet low + outlines (brief v1) | 80 | 9 | .867 | 81/83 |
| Haiku low + outlines (brief v1) | 83 | 15 | .900 | 84/86 |
| Sonnet low + outlines + lint (v2) | 84 | 12 | .901 | 85/86 |
| Haiku low + outlines + lint (v2) | 84 | 14 | .907 | 84/86 |
| **Sonnet low + outlines + lint (v3)** | **85** | **4** | **.914** | **85/86** |

| Held-out (82 typed rooms, brief v3 unchanged) | found | extra | mean IoU | types right |
|---|---|---|---|---|
| wall-model outlines alone | 79 | 6 | .906 | 35/77 |
| **Sonnet low + outlines + lint (v3)** | **79** | **4** | **.901** | **74/77** |
| Haiku low + outlines + lint (v3) | 79 | 20 | .903 | 66/77 |

What moved the numbers:
- Start from the wall model's outlines: they are already precise (median area error ~3%); the model's job is names,
  types, and fixing merges and splits. Drawing polygons from scratch costs 0.1-0.15 IoU.
- `lint` with OCR words: two room names in one outline -> split (open plans OH + RT + K, "K+R").
- v3 rule: SK, VK, LK are built-in cupboards, not rooms (v1/v2 had told the model to add them: 6 of 12 extras).
- On held-out plans the outlines already find the rooms; the model's gain there is types (35 -> 74 of 77) and fewer
  extras. Haiku adds rooms of partly drawn neighbouring units and leaves unnamed rooms untyped (a plan with no printed
  names: 2/9 types vs Sonnet 8/9), so Sonnet low is the setting to use.

Run (from the repo root, project venv): `prep_fix.py <outdir> <plans...>` builds start models, keys and engine answers;
agents write `model_final.json` per plan; `score_fix.py <fixdir> <keydir>`, `merge_test.py`, `roomfilter_test.py`
score them; `build_gallery.py` + `make_page.py` render the picture page. Room layer: `prep_heldout.py <outdir> <plans...>`
builds work folders (outlines + symbols + OCR); agents follow `briefs/brief_candidates_v3.md`; `room_eval.py <dirs>`
and `room_diag.py <dir>` score them against `gt/<plan>/model.svg` + `F1_scaled.png` (CubiCasa annotation files, not
in the repo).
