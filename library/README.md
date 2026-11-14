# Library: scan a paper drawing, get everything already known about it

Every drawing the engine has processed is kept: its image, the converted sheet, every answer with what it cost, and
every phone scan. A construction manager photographs the paper on site; the photo is matched to the stored sheet, the
other sheets of its project come up with it, and saved answers cost nothing the second time.

Live (Modal workspace `oliver-universityapps`): https://oliver-universityapps--bob-library-web.us-east.modal.run
Phone page: `/phone` · demo password `123456789` (one shared workspace, "Demo") · QR code for the phone page: `/api/qr.svg`.

## Pieces

| File | What |
|---|---|
| `modal_app.py` | the back end: FastAPI + a **MariaDB 11.8** server in the same container, photo matcher |
| `phone.html` | the phone page: password, camera, match, "this sheet / whole project", questions |
| `seed.py` | puts drawings in, with the engine's answers to the dataset's questions |
| `test_scan.py` | tests the matcher with simulated phone photos (tilt, rotation, blur, light, sheet cut off) |
| `ui/index.html` | QR button in the top bar (`ui/index.backup.html` is the page before this change) |

## How a photo is matched

1. A DINOv2 fingerprint (384 numbers) of the photo is compared with every sheet's fingerprint **inside MariaDB**:
   `sheets.embedding VECTOR(384)` with a `VECTOR INDEX ... DISTANCE=cosine`, queried with `VEC_DISTANCE_COSINE`. 24 candidates.
2. Each candidate is confirmed by local details (SIFT + RANSAC): how many details of the photo line up with the sheet under
   one perspective. This also gives the sheet's outline inside the photo.
3. Sure only when the best sheet has at least 25 matching details **and** 1.35 times more than the runner-up. Sheets of a
   set share title blocks and legends, and storeys of one building can be near copies; then the phone shows the
   look-alikes and the user picks.

Measured on 40 simulated photos against 62 stored sheets (3 multi-storey buildings + 40 Swiss plans): 26 matched right,
0 matched to a wrong sheet, 12 asked the user to pick, 2 not found. Median 1.3 s per scan on CPU. Not yet tested with a
real phone on printed paper.

## Tables (MariaDB)

`workspaces` → `projects` → `sheets` (image files on the volume, `sheet_json`, `embedding VECTOR(384)`, `revision`, `superseded_by`);
`answers` (question, normalised question, engine result, seconds, cost, hits, status pending/done);
`scans` (every photo: matched sheet, similarity, matching details, seconds, device); `ledger` (convert / answer / scan / reuse, cost and saved cost).
The database is dumped to the Modal volume `bob-library` after changes and restored when the container starts.

## API (header `x-bob-password`, or `?pw=`)

| Call | What |
|---|---|
| `POST /api/login` | checks the password |
| `GET /api/projects`, `GET /api/projects/{id}` | this workspace's projects; one project with its sheets |
| `POST /api/sheets?project=&name=&file_name=&revision=&supersedes=` | body = the file (PDF, PNG, JPG). A project name of this workspace is added to, a new name creates a project |
| `PUT /api/sheets/{id}/sheet` | body = the converted sheet JSON, so the drawing is never converted twice |
| `GET /api/sheets/{id}` (`?full=1` with the sheet JSON), `/thumb`, `/view`, `/original` | one sheet and its files |
| `POST /api/scan` | body = a photo. Returns `matched`, the sheet, its project, saved answers, a newer revision if there is one, or `look_alikes` |
| `GET /api/open/{id}?scan_id=` | the same package for a sheet the user picked |
| `POST /api/ask` | `{question, sheet_id}` or `{question, project_id}`: saved answers at once (per sheet + total), new ones queued |
| `GET /api/answers/{id}` | a queued answer's state |
| `GET /api/engine/next`, `POST /api/answers` | for the engine: next waiting question; store a computed answer |
| `GET /api/stats`, `GET /api/scans` | compute spent and saved; the scan log |

## Run

```bash
MODAL_PROFILE=oliver-universitapps modal deploy library/modal_app.py
python library/seed.py --project "Esplanades (5 floors)" --answers 10 junction-dataset-preview/data/ifc_models/bsc-esplanades_*plan*.pdf
python library/test_scan.py --n 40 <files that are in the library>
```

Not done yet: the engine answering queued (new) questions on Modal, storing the converted sheet for each drawing,
logins per company, revision upload from the page.
