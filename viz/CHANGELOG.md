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
| Evidence that isn't on this sheet (BIM model-wide answers) | yes | says "N of M on this sheet"; selecting one says "Not on this drawing" |
| Light theme matching the demo page | yes | default |

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
