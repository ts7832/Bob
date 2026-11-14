# Scoreboard

Shared test: the 241 preview scans, 4,270 count questions (`eval/test_scans.jsonl`). Counts must be exact.
Made by `eval/score.py`; do not edit by hand. Newest run of each engine, best first. Format and rules: `eval/README.md`.

| Engine | Who | Accuracy | Answered | Accuracy when answered | Confidence gap | Cost per question | When | Note |
|---|---|---|---|---|---|---|---|---|
| chooser-7 | tadek | **86.7%** | 100.0% | 86.7% | 2.9 pts | $0.00000 | 2026-11-14 19:47 | learned chooser over Aleks's 6 detectors and rival r5; cross-fitted on the test plans |
| chooser-9 | tadek | **86.6%** | 100.0% | 86.6% | 3.2 pts | $0.00000 | 2026-11-14 19:47 | chooser-7 plus the window judge and the window crop checker; cross-fitted on the test plans |
| drawqa-c26l+windowjudge+rival-r5 | tadek | **86.1%** | 100.0% | 86.1% | 2.2 pts | $0.00000 | 2026-11-14 19:34 | Aleks's engine; windows re-decided with the r5 map; then the more likely of that and rival r5 per question; cross-fitted on the test plans |
| drawqa-c26l+windowjudge+rival-r4 | tadek | **85.8%** | 100.0% | 85.8% | 1.8 pts | $0.00000 | 2026-11-14 18:45 | Aleks's engine; windows re-decided box by box with the rival map as second witness; then per question the more likely of that and rival r4; cross-fitted on the test plans |
| drawqa-c26l+rival-r4 | tadek | **85.4%** | 100.0% | 85.4% | 1.5 pts | $0.00000 | 2026-11-14 18:06 | per question, the more likely answer of Aleks's engine and rival r4 |
| drawqa-c26l+rival-r3 | tadek | **85.3%** | 100.0% | 85.3% | 3.1 pts | $0.00000 | 2026-11-14 17:38 | per question, the more likely answer of Aleks's engine and rival r3; agreement used as evidence |
| drawqa-c26l | aleks | **85.0%** | 100.0% | 85.0% | 12.6 pts | $0.00000 | 2026-11-14 15:04 | Aleks's engine, detector c26l with test-time augmentation |
| drawqa-c26l+calibrated | tadek | **85.0%** | 100.0% | 85.0% | 1.8 pts | $0.00000 | 2026-11-14 16:58 | same answers as drawqa-c26l, confidence recalculated (cross-fitted over plans) |
| drawqa-c26l+calibrated+abstain50 | tadek | **82.5%** | 91.9% | 89.7% | 1.8 pts | $0.00000 | 2026-11-14 16:59 | calibrated; says cannot determine under 50% confidence |
| rival-r5 | tadek | **81.6%** | 100.0% | 81.6% | 1.1 pts | $0.00000 | 2026-11-14 19:34 | density-map counting model, r3 settings, 40 epochs on H100 |
| rival-r3 | tadek | **78.8%** | 100.0% | 78.8% | 2.9 pts | $0.00000 | 2026-11-14 17:38 | density-map counting model, 20 epochs, last weights |
| rival-r4 | tadek | **58.2%** | 100.0% | 58.2% | 4.4 pts | $0.00000 | 2026-11-14 18:05 | density-map counting model, 40 epochs on H100, last weights |

Accuracy counts a missing or "cannot determine" answer as wrong. Confidence gap: the average distance between
the confidence an engine states and how often it is right at that confidence (0 is perfect).

## Accuracy by question kind

| Engine | balcony | bathroom | bedroom | door | floor | kitchen | sauna | shower_bath | sink | toilet | wc_room | window |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| chooser-7 | 94.1% | 93.7% | 87.0% | 78.4% | 97.9% | 87.6% | 97.6% | 85.5% | 87.9% | 96.2% | 95.5% | 53.9% |
| chooser-9 | 94.1% | 92.8% | 86.4% | 77.8% | 97.5% | 87.6% | 97.6% | 85.5% | 87.0% | 95.8% | 95.5% | 56.0% |
| drawqa-c26l+windowjudge+rival-r5 | 91.2% | 93.7% | 84.4% | 75.0% | 97.9% | 89.4% | 97.6% | 86.3% | 87.9% | 95.8% | 94.6% | 53.1% |
| drawqa-c26l+windowjudge+rival-r4 | 91.2% | 92.8% | 84.4% | 75.0% | 97.9% | 87.0% | 97.6% | 85.0% | 87.5% | 95.4% | 94.6% | 54.4% |
| drawqa-c26l+rival-r4 | 91.2% | 92.8% | 84.4% | 75.0% | 97.9% | 88.2% | 97.6% | 85.0% | 87.5% | 95.4% | 94.6% | 50.2% |
| drawqa-c26l+rival-r3 | 91.2% | 93.7% | 84.4% | 75.0% | 97.9% | 85.1% | 97.6% | 85.0% | 87.0% | 96.2% | 94.6% | 50.6% |
| drawqa-c26l | 91.2% | 92.8% | 84.4% | 75.0% | 97.9% | 82.0% | 97.6% | 85.0% | 87.5% | 95.4% | 94.6% | 50.2% |
| drawqa-c26l+calibrated | 91.2% | 92.8% | 84.4% | 75.0% | 97.9% | 82.0% | 97.6% | 85.0% | 87.5% | 95.4% | 94.6% | 50.2% |
| drawqa-c26l+calibrated+abstain50 | 91.2% | 91.0% | 83.1% | 72.7% | 97.9% | 79.5% | 97.6% | 83.7% | 86.2% | 95.4% | 94.6% | 35.7% |
| rival-r5 | 57.4% | 87.4% | 84.4% | 72.7% | 90.9% | 84.5% | 92.7% | 85.5% | 86.6% | 95.0% | 94.6% | 43.6% |
| rival-r3 | 57.4% | 86.5% | 77.9% | 69.3% | 86.3% | 85.7% | 91.5% | 80.2% | 81.6% | 92.1% | 96.4% | 42.3% |
| rival-r4 | 57.4% | 88.3% | 39.0% | 19.3% | 84.2% | 95.0% | 67.3% | 71.8% | 47.3% | 64.2% | 67.0% | 16.2% |

All runs: `scores/history.csv` (12 so far).
