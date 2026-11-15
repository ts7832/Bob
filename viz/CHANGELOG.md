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

## 2026-11-15 02:33: scan rooms split at the detected doors; no empty 3D on wall-less sheets
- **Scan rooms are now sealed at the detector's own door and window boxes** before rooms are found, so rooms split exactly at the doors. Before, the walls were thickened and some rooms merged through door gaps. Result on colorful-11397: tile only in the bathroom, oak in the bedroom.
- **No empty 3D on sheets without walls:** vector PDF pages (e.g. A477) draw walls as double lines that the view does not read. The 3D button is disabled there, with a tooltip saying why, instead of showing an empty tilted page. CAD, BIM and scan sheets keep 3D.
- `tests/qa_tour.py` only clicks an enabled 3D button.

## 2026-11-15 02:21: fixes from the judge-eye QA tour (every sheet in `ui/`: 2D, selected, 3D, Photo, clean view)
- **The 3D callout no longer duplicates and covers the detail card** in the team page's narrower view. It now appears when you hover an object in 3D; selecting one opens the detail card only.
- **The clean-view key is icons only** (names on hover), so it covers less of the drawing.
- **"Can't answer" reasons no longer end mid-sentence.** Example: A477's "...could not be turned into a query." now continues "This needs the language model, which is not running in this demo."
- **Tour:** `tests/qa_tour.py` makes one contact sheet per drawing. 0 page errors on all sheets.

## 2026-11-15 02:06: hover or click any object to see what it is (no question needed)
- **Every recognised object can now be inspected,** with or without an answer, in 2D and 3D:
  - **Hover:** shows its name and source, e.g. "WC · detected by a model · 85%", "Door · read from the CAD drawing", "Door · from the building model (IFC)".
  - **Click:** zooms in and opens a "What this is" card with its crop of the original. In 3D its model turns blue and the walls drop so it's visible.
  - **Esc** closes it.
- **Blue means inspecting, orange means an answer,** so the two are never confused.
- **The team page's "found on the drawing" chips** (kokorosf's `ui/`) already light up a whole class through `showAnswer`. With the new models, stairs, shafts and washers now show there too.
- **Harness:** clicking the shown question again clears it. The same toggle for `ui/` is requested on issue #1.
- **Test:** `tests/inspect_objects.py`.

## 2026-11-15 01:44: scans get whole rooms in 3D and the clean view
- **Scans had patchy 3D floors:** only the few rooms the detector boxes covered got a floor, and those boxes overlap walls. Now the rooms are found between the walls the view already reads from the image:
  1. door gaps are closed;
  2. the outside is dropped;
  3. each room is grown back to the real walls.
- **Each room's floor** takes its type from the detector box covering at least a quarter of it (bedroom: oak, bathroom: tile, …). Rooms the detector didn't type get a neutral floor. The clean view fills the same rooms.
- Display only, built on the first 3D or clean view, like the walls.

## 2026-11-15 01:36: a 3D model for every recognised symbol
- **Every class the backend recognises now has its own 3D model** (no generic blocks, nothing dropped). Furniture that is only drawn and not recognised (beds, tables on scans) stays visible as the drawing on the floor; nothing is invented.
  - **Stairs** (Swiss CAD and BIM): treads climbing toward the wall end, a landing on near-square stairs, and a handrail. BIM riser lines are skipped.
  - **Shafts:** a full-height duct with a grille.
  - **Washing machines:** a white cabinet with a porthole on the free side.
  - **Unlabelled CAD fixtures**, read by size:
    - posts up to 0.36 m;
    - appliance or cabinet units with a counter top;
    - wardrobes (≥1 m and long), 2 m tall with door seams.
  - **BIM furniture:** its own outline, extruded in wood.
  - **BIM railings:** their own outline. Balusters become chrome posts, longer rails glass with a chrome cap.
- **Cutaway clamps heights at the cut instead of scaling them.** Low things (a WC, a sill) keep their shape; tall ones (wardrobes, stairs, door leaves) are trimmed like a section. Counted objects are trimmed the same way.
- **New classes with glyphs, names and clean-view symbols:** washer, shaft, cabinet, railing. Counted washers, shafts and cabinets glow like the other symbols.
- **Harness:** `?focus=<ids>` shows sheet items (or BIM guids) as a test answer and flies to the first. **Test:** `tests/coldstart.py` checks the BIM cold-start crash (issue #31) on a fresh server.

## 2026-11-15 00:20: window frames, counted windows light up, Photo mode matches the new look
- **Windows in 3D** have frames: head and sill rails, jambs and a centre mullion around the glass.
- **Counted windows light up:** the pane turns orange in its opening and moves with its wall under the wall cut and the cutaway. Before, a counted window had no 3D highlight.
- **Photo mode** uses the same warm plaster, wet-room wall tiles, window frames and warm sun as the 3D view.
- **The clean-view key** is more compact, so it covers less of the drawing.

## 2026-11-15 00:12: new 3D look ("A + C" from the design canvas)
- Chosen from three directions on a Claude Design canvas: A's unmistakable answer highlight, plus C's real materials and callout.
- **Materials and light:**
  - warm plaster walls;
  - oak floors in rooms, stone in corridors and kitchens, grey-blue tile in wet rooms;
  - wet rooms also get a tiled band on their walls, 1.2 m high, only where there is a wall (doorways stay open);
  - a warmer, lower sun with soft long shadows.
- **The answer:** the counted object stays solid orange with a soft glow on the floor under it. In 3D the selected item gets a callout, e.g. "WC · 2 of 3 · fx79 · measured from geometry".
- **"Dim rest"** now fades out as the plan lifts. In 3D the orange models already stand out, so the room materials aren't washed out. 2D is unchanged.

## 2026-11-14 23:55: cutaway 3D, readable tags on dense answers, smoother big answers
- **Cutaway (3D, on by default):** walls on the camera's side drop low, and the back walls stay full height, so you can see into every room from any angle. It follows the camera as you orbit; shadows, door leaves and shower glass follow too. The **Cutaway** button switches it off.
- **Dense answers stay readable:** when tags would overlap on screen (e.g. 112 doors on a whole sheet), they are thinned out and come back as you zoom in. The selected or hovered item always keeps its tag, and every highlight stays.
- **Smoother:** symbol highlights are built one per task, so a 112-door answer no longer freezes the page (longest pause 594 → 187 ms).
- **Shared outlines:** the duplicate-outline note is now neutral, not a red warning. It states the fact ("items 2 & 85… share one outline; the count includes both"). Swiss door answers do this on all three samples: 112 cited for 98 outlines, 21 for 18, 23 for 20. The dataset counts them the same way.
- **Layout:** the toolbar wraps instead of running under the 3D note, which now sits above it.

## 2026-11-14 22:05: clean view keeps highlighted areas clean
- **Fix:** in clean view, a highlighted area (a room, a corridor) showed the original drawing inside it, even on the clean side. It now shows the clean drawing on the clean side and the original on the other, split exactly at the divider. The crop panel still shows the untouched original, for checking.

## 2026-11-14 22:05: Photo mode gets the real fixtures; BIM door leaves as drawn; TEAM.md
- **Photo mode** (path tracing) now renders the same fixture models as 3D: toilets, basins, tubs, showers, kitchens and door leaves. The counted object is a glowing model that lights its room. The old blocks are gone; clearcoat and alpha glass, which path-traced black, are replaced.
- **BIM door leaves:** each IfcDoor's own leaf rectangle from the model's plan is extruded to door height, open as drawn. A counted BIM door lights up as that exact leaf.
- **`TEAM.md`:** a one-page guide for teammates. It covers what the view does, which files are whose, the JSON fields the view reads, the open backend asks, and how to test a change against the view.

## 2026-11-14 21:50: rehearsal script and backup video; "can't answer" keeps the real reason
- **"Can't answer" cards keep the backend's real reason.** If the backend explains why and only then adds a note that the language model is offline, the card now shows the reason.
  - Example: "rooms are found as boxes by a detector, not as measurable outlines, and no scale is printed."
  - Before, the whole message was replaced by the generic "needs the language model" line.
- **`tests/demo.py`:** a scripted rehearsal in `ui/` with captions, checking each answer. It covers four offline-safe questions:
  - scan doors (12, detected);
  - Swiss WCs (3, measured) with 3D and the clean view;
  - total floor area on the scan (honest "can't answer": no scale);
  - A477 doors (12, two drawings, rotated page).
  - `--video` records the run (WebM, on the GPU). That's the backup video.
- **Backend regression:** offline, "What is the ceiling height?" no longer gets its deterministic "cannot determine" answer. It now says "language model on Modal not available", so don't use it in the offline demo.

## 2026-11-14 21:33: sheets with several drawings (task D)
- **Finds the drawings on a sheet** from the page itself: clusters of ink, with the border, grid lines and title block left out. Each one is titled from the sheet's own texts and printed scales. On scans, the detector's floors are the drawings.
  - Example: A477 gives "2ND & 3RD FLOOR - 1BD D - ADA TYPE B · 3/8" = 1'-0"" and the TYPE A plan.
- **Answer card:** "From the drawing <title>" when all the evidence is in one drawing. Otherwise it gives the split, e.g. "2 drawings: TYPE B (5), TYPE A (7)", with a reminder to check they don't show the same floor twice.
- **Drawings picker** in the toolbar: frames one drawing with a dashed outline and its title and scale.
- **Door highlights on dense CAD sheets** no longer spread along dimension lines. A door box that already holds the swing is used as it is.

## 2026-11-14 21:18: real 3D fixtures, whole-object highlights, rotated PDF pages fixed
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

## 2026-11-14 20:57: clean view, the plan redrawn from what was detected (task C)
- **Clean view** button (or **C**) in 2D: the plan redrawn from the system's own data, left of a swipe divider. The original drawing stays untouched on the right. Drag the divider to compare any spot.
- **What is redrawn:**
  - walls: exact on Swiss and BIM sheets, found in the image on scans;
  - rooms, tinted by kind (living, wet, kitchen/corridor, storage, balcony), with their name, number and area as the backend has them;
  - one symbol set: doors and windows cut into the walls, fixtures as boxes with their glyph. BIM symbols are drawn from their own paths.
- **Labels:** "Redrawn from detections" with a symbol key. Answers are drawn on top of both sides.
- **Where it is hidden:** in 3D, and on sheets with nothing detected (the judges' PDF sheets), so it never shows an empty redraw.
- **API:** `view.setClean(on, swipe?)` (swipe = divider position 0..1). Harness: `?clean=0.5`. Test: `tests/clean.py`.

## 2026-11-14 20:38: the drawn symbol lights up, not just its box (task B)
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
