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

Run (from the repo root, project venv): `prep_fix.py <outdir> <plans...>` builds start models, keys and engine answers;
agents write `model_final.json` per plan; `score_fix.py <fixdir> <keydir>`, `merge_test.py`, `roomfilter_test.py`
score them; `build_gallery.py` + `make_page.py` render the picture page.
