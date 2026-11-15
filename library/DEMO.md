# Bob: 2-minute demo script (office first)

Recorded by a person. Laptop screen capture for 0:00–1:25, phone filmed for 1:25–1:50, laptop again at the end.
Every click below was rehearsed. Print files: `~/BOB/demo/print/` (A4): 1 OLD Crema A-100 v2, 2 NEW bid set (for the upload, not printed if short of paper),
3 Swiss plan, 4 a spare scan. Rehearsed 2026-11-15: every step below ran end to end (headless browser + API).

## Before recording (15 min before)

1. Laptop server, from `~/BOB/repo/Bob-main`:
   `MODAL_PROFILE=oliver-universitapps PORT=8770 <venv>/python library/team_server.py`, then open http://localhost:8770
2. Warm the GPU, 5 minutes before. The first call can wait for a free GPU:
   `MODAL_PROFILE=oliver-universitapps <venv>/python -c "import sys; sys.path.insert(0,'.'); from drawqa import remote; print(remote.warm())"`
3. On the laptop, open the OLD Crema Cafe "A-100 Floor Plans v2" once (Your file → paste its path). This puts it in the library.
   Do NOT open the bid set yet.
4. Do NOT ask on colorful-2002 before recording (the circle step should start from 7). If it was corrected in a rehearsal,
   undo it: open it, "undo my last correction", or `POST /api/corrections/<id>/undo`.
5. Ask the Swiss plan questions below once, so the recording is not waiting on a cold engine. Answers come back
   from the library ("FROM LIBRARY · $0").
6. Phone: open the QR (top right) and log in with 123456789. Lay the printed OLD A-100 v2 (print file 1) on a table, in good light.

## Script

| time | screen | do | say (short) |
|---|---|---|---|
| 0:00 | laptop | Swiss plan (print file 3) is open. Ask "How many doors are on this floor plan?" | "Ask a blueprint anything. Every number is computed by code and marked on the plan." |
| 0:12 | laptop | Open "How this answer was made" | "Two question readers agree, no language model, $0, in a second." |
| 0:22 | laptop | Ask in Finnish: "Kuinka monta ikkunaa tässä pohjapiirroksessa on?" | "Any language: our small trained router reads it." |
| 0:32 | laptop | Open the scan colorful-2002 (Your file → paste `.../cubicasa5k/colorful-2002.png`). Ask "How many doors are shown on this floor plan?" → 7 | "On scans the models can miss or invent things, so a person checks." |
| 0:42 | laptop | ◯ Circle a problem → circle mark 7 (top-left of the blue bathroom PH) → comment "is this really a door?" → Send to Opus | "Circle it. Opus looks at only that part and the marks counted there..." |
| 0:55 | laptop | Opus: "remove door: dashed arc, no door leaf" → Accept, close → answer 6 (the right answer), CORRECTED | "...a person accepts, and the correction is kept for everyone, with who and why." |
| 1:05 | laptop | Your file → drop "A-100 bid set" (print file 2) → banner "NEWER REVISION?" → Yes → Show what changed | "A new revision is recognised from the drawing itself. Red was removed, blue is new." |
| 1:25 | phone | Scan the printed OLD A-100 → "This paper is out of date" + what changed | "On site, the phone knows the paper in your hand is out of date." |
| 1:38 | phone | Scan the printed Swiss plan (print file 3) → ask "How many doors are on this floor plan?" → 18 | "And it answers right there, from the library, $0." |
| 1:50 | laptop | History → the phone question and photo are there. Then the money counter. | "Every question and photo is kept. €X of take-off work saved; AI cost avoided $Y." |
| 2:00 | – | end | |

## If something goes wrong

- The answer spins for more than 20 s: the GPU is cold. Wait, or cut that part and use a library (repeat) question.
- The phone shows "pick the blueprint": two library sheets look the same. Delete test copies:
  `curl -X DELETE -H "x-bob-password: 123456789" https://oliver-universityapps--bob-library-web.us-east.modal.run/api/sheets/<id>`
- Never redeploy `bob-library` during recording: its database runs in one container.

## Known limits seen in rehearsal (say them if asked)

- US drawing sets: door counts on a large CAD sheet with several small plans come back 0 ("no element found by the
  trained detector"); that should read "cannot determine". Do not count doors on the Crema sheet in the demo.
- Opus declines when the drawing does not show what the note claims (tried twice on purpose): that is the honesty we want.
- Modal L4 GPUs ran out once (minutes of waiting); the service now falls back to A10G / L40S.
