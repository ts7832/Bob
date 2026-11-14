# Jev-style router (small): first test, 2026-11-15

Question in any wording or language -> calibrated probability of what is asked (25 meanings: count:door, area:one_room,
area:whole_floor, count:rooms_of_a_type, ..., and "other" for questions a blueprint cannot answer). No image.
Code: `modal_router.py` (kokoro-63208 workspace). Weights: volume `bob-data`, `/jev/router1/`.

| Data | How |
|---|---|
| Labels | the engine's pattern router read 6,072 dataset questions; 549 it cannot read borrowed the label of the same question in another language (same drawing, same answer) |
| Rewordings | Qwen2.5-7B-Instruct (open model, vLLM on Modal): 8 per source question - casual, short, formal, typo, Finnish, German, French, Swedish; 176 out-of-scope questions |
| Model | multilingual-e5-small (118M), fine-tuned, temperature scaling on validation questions |

| Test (never seen in training) | Jev-style router | Pattern router |
|---|---|---|
| 1,032 rewordings of held-out questions | **88.9%** (confidence gap 4.2 points; 87.6% of answers at >= 80% confidence, 97.2% right there) | 28.4% (716 not read at all) |
| 549 real dataset questions the pattern router cannot read (German, Dutch, Spanish, Romanian) | **78.7%** (a lower bound: of 77 checkable by keyword, 12 borrowed labels are wrong) | 0% |

Report: `router1_report.json`. Next: per-row predictions, cleaner labels for the hard set, argument extraction (which room,
which storey), and the cascade (patterns -> this model -> Opus below a confidence threshold).
