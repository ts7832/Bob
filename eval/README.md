# Shared scoreboard

One test set, one answer format, one scoring script, so engines built separately (Aleks's `drawqa`, the rival
counting model, any mix of the two) can be compared like for like. Results: [`scores/README.md`](../scores/README.md).

This sits next to the other two scoreboards and does not replace them: `drawqa/eval.py` (drawqa only, all sources)
and `gold/` (hand-checked judge-style questions).

## Test set

`eval/test_scans.jsonl`: the 4,270 count questions on the **241 preview scans** (CubiCasa5k), in English and Finnish.
It is the same set `drawqa/eval.py --source cubicasa5k` scores, so numbers match the main README.

**Nobody trains or tunes on these 241 files.** The file list is the `path` column of `test_scans.jsonl`.

Each line:

```json
{"id": "s0000", "path": "data/cubicasa5k/colorful-11397.png", "question": "How many floors (storeys) does this floor plan show?",
 "answer": 2, "unit": "count", "type": "count", "kind": "floor", "lang": "en"}
```

`kind` is one of: balcony, bathroom, bedroom, door, floor, kitchen, sauna, shower_bath, sink, toilet, wc_room, window.
Rebuild with `python eval/build_test_set.py <full dataset>/questions.jsonl`.

## Answer file format

One JSON object per line, one line per question. Lines can be in any order. A question with no line counts as not
answered.

```json
{"id": "s0000", "answer": 2, "confidence": 0.88, "tier": "detected", "method": "yolo:c26l", "cost_usd": 0.0, "seconds": 0.05}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | the question's `id` from `test_scans.jsonl` |
| `answer` | yes | a number, or `null` for "cannot determine" |
| `confidence` | no | 0 to 1: the probability that `answer` is exactly right. Leave out if the engine has none |
| `tier` | no | `read`, `measured`, `detected`, `estimated` or `cannot determine` (the honesty tiers of `drawqa/solve.py`) |
| `method` | no | free text: which model or rule produced the answer |
| `cost_usd` | no | what this answer cost in paid model calls and GPU time; `0.0` when it ran locally for free |
| `seconds` | no | time taken |
| `distribution` | no | probability of each possible count, e.g. `{"3": 0.10, "4": 0.81, "5": 0.09}` |
| `evidence` | no | boxes or points that show what was counted, pixel coordinates of the image |

The script rejects a file with a missing `id` or `answer`, an unknown `tier`, a `confidence` outside 0 to 1, or an `id`
that is not in the test set.

## Scoring

```bash
python eval/score.py answers/my-engine.jsonl --name my-engine --who me --note "what changed"
python eval/score.py answers/my-engine.jsonl --name my-engine --who me --no-record     # print only
```

Standard library only. Each recorded run appends a line to `scores/history.csv` and rewrites `scores/README.md`,
which shows the newest run of each engine name. Commit the answer file in `answers/` with the run.

- **Accuracy**: counts must be exact (the dataset's rule). `null` or a missing line is wrong.
- **Answered** and **accuracy when answered**: how often the engine gave a number, and how often that number was
  right. An engine that abstains scores lower on accuracy and higher on accuracy when answered; both are shown so the
  two ways of handling doubt can be compared.
- **Confidence gap**: answers are grouped by stated confidence in steps of 0.1; the gap is the average distance
  between the stated confidence and the share that was right. 0 means "when it says 90%, it is right 90% of the
  time". The script prints the full table.
- **Brier**: mean squared error of the confidence against right or wrong. Lower is better.
- **Cost per question**: mean of `cost_usd`.

Which engine wins a question kind: higher accuracy; when two are within about 2 points, the smaller confidence gap.

## Scoring drawqa

```bash
python eval/export_drawqa.py answers/drawqa-c26l.jsonl      # uses the detector named in data/predictions/active.txt
python eval/score.py answers/drawqa-c26l.jsonl --name drawqa-c26l --who aleks
```
