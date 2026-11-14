# Scoreboard

Shared test: the 241 preview scans, 4,270 count questions (`eval/test_scans.jsonl`). Counts must be exact.
Made by `eval/score.py`; do not edit by hand. Newest run of each engine, best first. Format and rules: `eval/README.md`.

| Engine | Who | Accuracy | Answered | Accuracy when answered | Confidence gap | Cost per question | When | Note |
|---|---|---|---|---|---|---|---|---|
| drawqa-c26l | aleks | **85.0%** | 100.0% | 85.0% | 12.6 pts | $0.00000 | 2026-11-14 15:04 | Aleks's engine, detector c26l with test-time augmentation |

Accuracy counts a missing or "cannot determine" answer as wrong. Confidence gap: the average distance between
the confidence an engine states and how often it is right at that confidence (0 is perfect).

## Accuracy by question kind

| Engine | balcony | bathroom | bedroom | door | floor | kitchen | sauna | shower_bath | sink | toilet | wc_room | window |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| drawqa-c26l | 91.2% | 92.8% | 84.4% | 75.0% | 97.9% | 82.0% | 97.6% | 85.0% | 87.5% | 95.4% | 94.6% | 50.2% |

All runs: `scores/history.csv` (1 so far).
