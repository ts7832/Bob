# Gold set: judge-style questions with hand-checked answers

The dataset's `questions.jsonl` was written by people and some answers are wrong, and most of it covers the preview
scans and Swiss plans that `drawqa/eval.py` already scores. This folder is the other scoreboard: a small set of
questions that look like what the judges will do (one file they hand over, several numeric questions), with answers a
team member checked on the sheet, and with the honesty tier we expect the system to report.

Owner: the evaluation / demo pair (not Session A or B). Files here and `tools/gold_*.py` are ours; we do not edit
`drawqa/` or `demo/`.

## Files

| File | What |
|---|---|
| `gold.jsonl` | one question per line (format below) |
| `candidates.csv` | output of `tools/gold_pick.py`: drawings ranked as gold candidates |
| `reports/` | output of `tools/gold_eval.py`, one JSON + Markdown pair per run (git-ignored) |

## Format of a line in `gold.jsonl`

```json
{"id": "g001",
 "path": "data/opening_design/.../A-801 - Door and Finish Schedules & Wall Types.pdf",
 "question": "How many doors are listed in the door schedule?",
 "type": "count",                 // count | area | length | volume
 "unit": "count",                 // count | m2 | ft2 | m | ft | m3 | ft3
 "answer": 38,                    // the checked answer; null with status verified = "cannot determine" is correct
 "expected_tier": "read",         // read | measured | detected | estimated | cannot determine
 "status": "verified",            // needs_answer | proposed | verified | rejected
 "proposed_answer": 38,           // a draft value someone (or an agent) read off the sheet, not yet checked
 "proposed_by": "claude",
 "evidence_note": "door schedule, top left of the sheet; count the rows with a door number",
 "origin": "dataset",             // dataset | judge-style | honesty
 "dataset_answer": 38,            // the label in questions.jsonl when the question comes from there, else null
 "verified_by": "kokoro",
 "note": ""}
```

Rules:

1. **Only `verified` lines are scored by default.** A `proposed` value is a draft: it may come from an agent reading
   the sheet and must be checked by a person on the drawing before the status changes. Never copy
   `dataset_answer` into `answer` without checking it: the dataset's labels are known to contain mistakes.
2. **Honesty questions** (`origin: honesty`) have `answer: null` and `expected_tier: "cannot determine"`: the
   correct behaviour is to refuse, e.g. an area on a sheet without a scale.
3. **When the checked answer differs from `dataset_answer`**, say why in `note`. These are the "the human label was
   wrong, here is the proof" cases for the demo.
4. **Units**: give `answer` in the unit the drawing uses (ft2 on a US sheet). The scorer converts the system's
   answer when its unit differs.
5. Do not put the 241 preview scans or the 65 preview Swiss plans here for tuning; they are the other scoreboard's
   test sets.

## Run

```bash
.venv/bin/python tools/gold_pick.py                 # -> gold/candidates.csv
.venv/bin/python tools/gold_eval.py                 # verified lines only
.venv/bin/python tools/gold_eval.py --drafts        # also score proposed values (marked as drafts in the report)
.venv/bin/python tools/gold_eval.py --llm           # let solve() use the language model for free wording
```
