# Hybrid routing: engine fast path when both routers agree, answer agent otherwise

Status: proposal, agreed by Tadek (2026-11-15). Aleks owns `drawqa/ask.py`. This doc names the change. It does not make it.
Code referenced here lives on branch `scan-library` (`library/jev_cascade.py`, `library/modal_engine.py`, `library/eval_*.py`).

## Why

Today `ask()` sends every drawing question to the answer agent (`use_engine` is off unless the input is a building model or `DRAWQA_USE_ENGINE=1`).

The agent is the right tool for wording the engine has never seen. It costs a language-model call per question, though, and judges score cost (25%).

On wording the engine knows, it is already about 99% right for $0 in under a second.

The hybrid keeps both and picks per question, using a signal we measured:

- Two independent readers of the question: the pattern router (`drawqa/router.py`) and the Jev-style router.
  - The Jev-style router is a small classifier: multilingual-e5-small plus a linear head, calibrated, trained on Haiku rewordings in 5 languages.
- Whether the two readers agree.
- A combined confidence.

## The rule

```
patterns read it  AND  Jev-style router gives the same meaning  AND  combined >= 0.6  AND  no better layer class
    -> engine answer (route="engine"), $0
otherwise
    -> answer agent over the layer (route="agent"), with both readings passed in `note`
```

- The combined confidence is: `engine confidence x router probability x (0.5 if the routers disagree)`.
- "No better layer class" is the existing `better` check in `ask()`, and it stays.
- For raster sheets, counts of openings and fixtures keep coming from the checked layer, as `ask()` does now, not from the raw engine. Scans need this: on the fast path, scans are 90.1% right against 99.1% for Swiss plans (table below).

## Measured (stored runs, `library/eval_out/cascade_1115_0128.jsonl.gz`, router2)

| test set | all questions right | fast path takes | fast path right |
|---|---|---|---|
| dataset wording (44,964 questions) | 96.6% | 77.5% | 98.7% |
| Haiku rewordings (held-out sources) | 81.9% | 31.1% | 98.5% |
| Qwen rewordings (harder, noisier) | 57.9% | 22.3% | 94.8% |

The fast path takes far fewer reworded questions, and more of the work goes to the agent. That is the intended behaviour.

Fast path by source, dataset wording, threshold 0.6:

| source | questions | right |
|---|---|---|
| Swiss | 30,969 | 99.1% |
| building models | 2,305 | 98.4% |
| US sets | 15 | 100% |
| scans | 1,580 | 90.1% |

The 90.1% on scans is why their counts keep coming from the layer.

Effect of each threshold on dataset wording:

| threshold | fast path takes | fast path right |
|---|---|---|
| 0.0 | 93.5% | 97.8% |
| 0.5 | 81.2% | 98.5% |
| 0.6 | 77.5% | 98.7% |
| 0.8 | 71.5% | 98.9% |

0.6 is the knee.

When the routers disagree, the pattern reading is right only 84.3% of the time, against 97.7% when they agree. So disagreement is the cheapest warning sign we have, and it should send the question to the agent.

## Interface (all in `library/jev_cascade.py`, no change to drawqa files needed to use it)

```python
from library import jev_cascade                    # or copy the file next to drawqa
jev_cascade.install("/path/to/router3")            # patches drawqa.router.parse; both routers read every question
r = solve(path, question, generic=True)            # unchanged call
how = jev_cascade.last()
# how = {by: "pattern router" | "jev router" | "jev router (override)" | "nobody",
#        label, probability, top: [(label, p) x3], pattern_meaning, agree: True/False/None, alt: intent or None}
combined = r["confidence"] * how["probability"] * (0.5 if how["agree"] is False else 1)
fast = how["by"] == "pattern router" and how["agree"] and combined >= 0.6
```

Suggested change in `ask()`, after `solve()`:

```python
known = known and fast                              # was: known
note = f"pattern router: {how['pattern_meaning']}, Jev-style router: {how['label']} ({how['probability']:.0%})" ...
```

Model files:

- router2 on Modal volume `bob-dataset` at `/jev/router2` (workspace oliver-universityapps), and on `bob-data` at `/jev/router2` (workspace kokoro-63208).
- router3, with extra rare types and a `table` label: same places, `/jev/router3`.

Runtime: torch 2.5.1 and transformers 4.47.1 (other versions gave constant outputs). Runs on CPU; no GPU needed.

Labels (26 + `table` in router3):

- Counts: `count:door|window|wall|stair|column|beam|all_rooms|rooms_of_a_type|apartments|floors`, `count:fixtures:wc|sink|bathtub|shower|shower_or_bathtub|any`.
- Areas: `area:one_room|whole_floor|apartment|rooms_of_a_type|wall`.
- Other quantities: `length:wall`, `volume:wall|slab`.
- `table`: asked about a schedule or list printed on the sheet.
- `other`: out of scope, so the agent or the language model handles it.

## Audit trail

Both readings, agree/disagree, and the combined confidence are already written into the answer's audit, in `library/modal_engine.py` `_router_audit`:

- step "Question read": who read it, and the top 3 meanings with probabilities.
- step "Overall confidence": engine x reading, x 0.5 on disagreement.
- `audit.routers`: `{by, jev_label, jev_probability, pattern_meaning, agree}`.

The UI's "How this answer was made" panel shows them. Add a step "Route: engine fast path / answer agent (why)" so the judges can see why a language model was or wasn't used.

## Also on scan-library (FYI)

- **Scans with no stored detector output** used to answer 0 at 50% (`scan.load`: "No detector predictions for this plan yet"). `jev_cascade.answer()` now sets `DRAWQA_LIVE=1` for them, so Aleks's detector runs on the `drawqa-infer` GPU (deployed in oliver-universityapps). Tested on `colorful-10052.png`: 5 doors, correct, 10 s warm.
- **Table questions:** with router3, the `table` label sends a question to `table_answer` even when `names_a_table` does not match the wording.
