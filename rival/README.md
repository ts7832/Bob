# Branch `rival`: what is here (state at 2026-11-14 evening)

Work done separately from the main engine (`drawqa/`), which this branch does not edit. Scores: [`scores/README.md`](../scores/README.md).
Format of answer files and how to score one: [`eval/README.md`](../eval/README.md).

| Folder / file | What |
|---|---|
| `eval/score.py`, `eval/test_scans.jsonl` | shared scoreboard: 4,270 count questions on the 241 preview scans |
| `eval/calibrate.py` | recalculates the confidence of an answer file (answers unchanged). drawqa: gap 12.6 -> 1.8 points |
| `eval/combine.py` | per question, the more likely answer of two engines |
| `eval/box_judge.py` | detector boxes re-decided with the rival model's map as a second witness (windows 50.2% -> 54.4%) |
| `eval/judge_plus_opus.py`, `rival/opus_check.py` | experiments: Opus as a second opinion on unsure window boxes (no reliable gain) |
| `rival/modal_rival.py` | rival counting model (density maps) on Modal: prep, train, predict. Best run r3: 78.8% alone |
| `rival/overlay.py` | draws what the rival model counted on a plan |
| `rival/modal_cad.py`, `rival/cad_overlay.py` | door detector for CAD-style sheets, trained on FloorPlanCAD with corrected labels; tested by eye on the DfE school plan only (no answer key) |
| `rival/modal_fpcad.py` | FloorPlanCAD download |
| `hard/modal_ocr.py` | tiled text reading (superseded by `drawqa/textlayer.py` on `freeform-tools`) |
| `rival/out/`, `hard/out/` | outputs of the experiments above |

Modal: workspace `kokoro-63208`, volume `bob-data` (`/junction-dataset`, `/cubicasa5k`, `/rival`, `/fpcad`, `/cad`).
Known limit: the box judge and the combiner are cross-fitted on the preview plans (each plan decided by a model fitted on
the others), because detector boxes exist in the repo for those plans only.
