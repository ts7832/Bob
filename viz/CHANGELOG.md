# DrawView changelog

The drawing view (`viz/`): what exists, and what changed when. Newest first. Usage and the API are in `README.md`.

## Already built: please reuse instead of rebuilding

| Need | Already in the view | How |
|---|---|---|
| Show any sample or uploaded drawing (scan, PDF page, Swiss DXF, BIM SVG, full A1/A2 sheets up to ~140 MP) | yes | `view.open(path)` |
| Show an `/api/ask` answer on the drawing, styled by honesty tier | yes | `view.showAnswer(res, question)` |
| Select one evidence item: zoom to it plus a crop of the original | yes | `view.select(id)`, `view.onSelect(cb)` |
| Step through every evidence item (count checks) | yes | ← → keys, or ‹ › in the answer card |
| Measure lengths and areas on the drawing (scale source stated) | yes | Measure button / M |
| Hide all highlights to see the original | yes | Original button (hold) / Space |
| Breakdown answers (areas by type), baseboards, slabs, pipes | yes | optional evidence fields: README "Optional evidence fields" |
| 3D lift with walls, windows, fixtures, room-aware floors; photo render | yes | 3D / Photo buttons, `view.setMode("3d")` |
| Symbol-accurate highlights (the drawn door / WC / window glows), class glyphs, double-count note | yes | automatic for symbol classes; README "How evidence is drawn" |
| 3D fixture models (WC, basin, tub, shower, kitchen, sauna, door leaves); counted ones light up | yes | 3D button |
| Rotated PDF pages: backend positions turned to match the page image | yes | automatic (`frameFix` in adapter.ts) |
| Several drawings on one sheet: which one an answer comes from, a picker to frame each | yes | automatic; Drawings picker in the toolbar |
| Clean view: plan redrawn from detections, swipe compare with the original | yes | Clean view button / C, `view.setClean(on, swipe)` |
| Evidence that isn't on this sheet (BIM model-wide answers) | yes | says "N of M on this sheet"; selecting one says "Not on this drawing" |
| Light theme matching the demo page | yes | default |

## 2026-11-15 00:55: sheets with several drawings (task D)
- **Finds the drawings on a sheet** from the page itself: clusters of ink, with the border, grid lines and title block left out. Each one is titled from the sheet's own texts and printed scales. On scans, the detector's floors are the drawings.
  - Example: A477 gives "2ND & 3RD FLOOR - 1BD D - ADA TYPE B · 3/8" = 1'-0"" and the TYPE A plan.
- **Answer card:** "From the drawing <title>" when all the evidence is in one drawing. Otherwise it gives the split, e.g. "2 drawings: TYPE B (5), TYPE A (7)", with a reminder to check they don't show the same floor twice.
- **Drawings picker** in the toolbar: frames one drawing with a dashed outline and its title and scale.
- **Door highlights on dense CAD sheets** no longer spread along dimension lines. A door box that already holds the swing is used as it is.

## 2026-11-15 00:10: real 3D fixtures, whole-object highlights, rotated PDF pages fixed
- **Rotated PDF pages: highlights were in the wrong place, now fixed in the view.**
  - On PDFs whose page has /Rotate (A477, Springhaven P100/P101, 1810 S Park St: 3 of the 5 real-project gold sheets), the page image is turned, but every backend position was in the unturned page frame: texts, rooms, door arcs and `/api/ask` evidence. Example: A477 "12 doors" cited boxes at y = 5253 on a 4500 px tall page.
  - The view now detects this, tests both quarter turns against the ink in the page image, and turns all positions to match. The card says so.
  - If it cannot match, it highlights nothing and says why, instead of drawing in the wrong place. Evidence that lies off the page is never drawn either.
  - The proper fix belongs in the backend: transform PyMuPDF coordinates by `page.rotation_matrix`.
- **Whole-object highlight:** a counted door, WC, basin or window lights up as one filled shape (a door's whole swing, a WC's tank and bowl) with a soft glow. Its tag is a pin that points at it.
- **3D fixtures are real models now, not blocks:** toilets (cistern and bowl), basins on vanities with taps, bathtubs with a hollow tub, showers (tray, glass on the open sides, pole and head), kitchen units (counter, hob, sink), sauna benches and stoves, and door leaves.
  - Each model is sized from its detected box and turned to the nearest wall.
  - Door leaves hinge on the side where the drawing shows the swing.
  - Scans now get door leaves too.
- **3D evidence:** the counted object's own model lights up in the answer colour; the translucent cubes are gone. Selecting a symbol in 3D lowers the walls so it is never hidden.
- **Harness:** `?path=<server path>&ask=<question>` opens any drawing the server knows and asks live.

## 2026-11-14 22:30: clean view, the plan redrawn from what was detected (task C)
- **Clean view** button (or **C**) in 2D: the plan redrawn from the system's own data, left of a swipe divider. The original drawing stays untouched on the right. Drag the divider to compare any spot.
- **What is redrawn:**
  - walls: exact on Swiss and BIM sheets, found in the image on scans;
  - rooms, tinted by kind (living, wet, kitchen/corridor, storage, balcony), with their name, number and area as the backend has them;
  - one symbol set: doors and windows cut into the walls, fixtures as boxes with their glyph. BIM symbols are drawn from their own paths.
- **Labels:** "Redrawn from detections" with a symbol key. Answers are drawn on top of both sides.
- **Where it is hidden:** in 3D, and on sheets with nothing detected (the judges' PDF sheets), so it never shows an empty redraw.
- **API:** `view.setClean(on, swipe?)` (swipe = divider position 0..1). Harness: `?clean=0.5`. Test: `tests/clean.py`.

## 2026-11-14 21:15: the drawn symbol lights up, not just its box (task B)
- **Counts of doors, windows, WCs, sinks, showers, bathtubs, stairs, columns:** the symbol as drawn on the sheet glows in the answer colour.
  - A door shows its leaf and swing, even though the detector box or opening polygon only covers the gap in the wall.
  - A WC shows its bowl. A window shows its glazing lines.
  - The box stays as a thin outline (dashed when detected).
- **Same style on every sheet type:**
  - Scans and PDF pages: strokes found in the image inside the box, with wall fill removed.
  - Swiss DXF sheets: the same method on the CAD raster.
  - BIM plans: the element's own SVG paths, so it is exact.
- **Class glyphs:** each tag shows a small plan-symbol glyph next to its number and sits beside the box, so it never covers the symbol. The answer card names what is highlighted ("doors", or "2 showers, 1 bathtub"). The detail card has a "Symbol" row.
- **Possible double counts:** a count whose evidence repeats an outline gets a note in the card. Example: the Swiss sample's 21 doors cite three pairs of identical door polygons (items 1 & 21, 11 & 19, 13 & 20). The dataset answer is also 21, so this may be intended, but now a reviewer can see it.
- **Fix:** BIM evidence ids no longer get a stray `#2` suffix, so `view.select(guid)` from the page finds BIM items again.
- **Fix:** BIM door boxes now cover the whole swing, also on rotated plans (Esplanades). SVG arcs are sampled instead of reduced to their end points.
- How it works: README "How evidence is drawn".

## 2026-11-14 20:10: fix for a server crash on BIM plans
- Opening a BIM plan in `ui/` crashed `tools/ui_server.py` with a segmentation fault (exit 139), every time on Esplanades.
  The view fetched `/api/plan` and `/api/sheet` in parallel, so two threads ran `ifcopenshell.open()` on the same model
  at once (`drawqa/ifc.py _model` via `plan_storey`). ifcopenshell is not thread-safe.
- The view now fetches render, plan and sheet one after the other. This is not slower: the second request is served
  from the server's cache. Re-tested: all 7 samples plus a gold drawing in `ui/`, server stays up, 0 errors.
- Still needed on the backend: a lock around IFC model loading in `drawqa/ifc.py`, because any two parallel requests
  (two browser tabs, or the page plus the gold scorer) can still hit it.

## 2026-11-14 18:00: works inside the team page; off-sheet evidence explained
- Tested inside `ui/index.html` (`tools/ui_server.py`, PR #9) with this bundle: all 7 samples plus a gold drawing, rehearsed questions, evidence list to view selection, 3D. 0 console errors (`tests/teampage.py`).
- Keyboard shortcuts only act when nothing else on the page has focus. The page's own buttons and inputs keep Space and the arrow keys.
- Selecting an evidence item that is not on this sheet (BIM answers count the whole model) now shows "Not on this drawing" instead of doing nothing.

## 2026-11-14 17:45: verification tools (researched from takeoff and BIM viewers)
- Researched Bluebeam (markups list, measure, overlay), the Autodesk viewer (measure, isolate, section box), xeokit (section planes, X-ray), Matterport (dollhouse, measure) and Togal.AI (AI takeoff plus human review). Built the tools that help someone verify an answer in seconds:
  - evidence stepper with ‹ › and arrow keys (zoom plus crop per item)
  - measure tool (M) with snapping; units come from the sheet's own scale, and the readout says which scale
  - Original (hold) or Space: the untouched drawing
  - double-click zoom; F to frame again
  - legend rows toggle groups
  - 3D wall cut-height slider
- The test harness's Ask box now goes to the real backend.

## 2026-11-14 17:15: light theme
- Tokens from `demo/index.html`. The answer number is styled like the page's `.big`. Toolbar bottom-left, note bottom-right.
- "Dim rest" is now a light veil.
- Zoom-to-answer fits the evidence into the space the cards leave free.

## 2026-11-14 17:00: review fixes
- `npm ci` works (`.npmrc legacy-peer-deps=true`).
- A drag no longer counts as a click.
- Open lines stay open in the crop.
- A plan item with no geometry no longer fails the load.
- No fake walls from text on full sheets.
- 3D geometry is built only when 3D is first used.
- Performance safety net (AO and shadows drop on slow machines); `view.setQuality()`.
- Esc deselects.
- Plain-language "can't answer" text.

## 2026-11-14 16:10: harder sheets (judges' examples)
- Optional evidence fields: `group`, `breakdown`, `value`/`unit`, `shape: "polyline"`, `thickness_m`, `diameter_m`. These give areas by type, baseboards, slabs at their printed thickness, and pipes at their printed size.
- Full-size sheets: PNGs of 67-138 MP decode downscaled when needed.

## 2026-11-14 15:50: realistic 3D and photo mode
- Studio light, soft shadows and ambient occlusion.
- Windows (sill, glass, lintel), door lintels and fixtures, all from the drawing's own data.
- Room-aware floors: the floor type comes from the room's printed name, in the dataset's languages.
- Photo mode: a GPU path tracer.

## 2026-11-14 15:30: 3D lift
- `setMode("3d")`. Walls come from Swiss DXF `WALL`, BIM `IfcWall` / `IfcWallStandardCase`, or the scan image.
- The wall tops are the drawing itself, so 2D → 3D → 2D returns the same picture.

## 2026-11-14 15:07: first version (PR #5)
- One bundle, `dist/drawview.js`, built against the demo's real responses for its 7 samples.
- API: `mount`, `open`, `load`, `showAnswer`, `clear`, `reset`, `select`, `onSelect`, `onLoad`, `setMode`, `destroy`.
