# DrawView: the drawing view for the demo

The left 75% of the demo: the drawing, with the answer to a question shown on it. Every highlight sits on its exact spot on the original sheet, and clicking one shows a crop of the original with its ID, position and confidence. One script, `dist/drawview.js` (React, three.js and CSS inside, no CDN, works offline). It reads only the demo's existing endpoints: `/api/render`, `/api/plan`, `/api/sheet` and `/api/ask`.

## Use it in demo/index.html

```html
<script src="/viz/drawview.js"></script>   <!-- see "Serving" below -->
<script>
  const view = DrawView.mount(document.getElementById("viewer"));   // once; don't clear #viewer afterwards

  // open(d):       instead of fetching /api/render + /api/plan and drawing the SVG
  await view.open(d.path);
  // ask():         after the /api/ask response r arrives
  view.showAnswer(r, question);
  // clearHl()  -> view.clear();   resetView() -> view.reset()
  view.onSelect(el => { /* el.id is the evidence id from /api/ask, or null when deselected */ });
</script>
```

| Call | What it does |
|---|---|
| `DrawView.mount(el, {base?})` | Creates the view inside `el` (fills it; give `el` a height). `base` prefixes `/api` URLs if the API is elsewhere. |
| `view.open(path)` | Loads a drawing: fetches render + plan + sheet. Shows what the backend found (faint outlines) until a question is asked. |
| `view.load({render, plan, sheet, imageUrl})` | Same, from JSON you already have (tests, replay). |
| `view.showAnswer(askJSON, question?)` | Highlights the evidence, styled by honesty tier; shows the value, tier and basis in a badge. Safe to call while a drawing is still loading. |
| `view.clear()` / `view.reset()` | Remove the answer / fit the whole sheet. |
| `view.select(id)` / `view.onSelect(cb)` | Select an evidence item (zooms to it, shows the crop) / get told when the user does. |
| `view.onLoad(cb)` | Called with `{render, plan, sheet}` after a drawing loads (e.g. to build a legend without fetching twice). |
| `view.setQuality("auto" \| "high" \| "low")` | 3D quality. `auto` (default) turns off ambient occlusion and shadows and lowers resolution when the frame rate stays low. |
| `view.setMode("2d" \| "3d")` | Lifts the sheet into 3D and back (also a 2D/3D button in the view). Heights are illustrative only, never used for answers. |
| `view.destroy()` | Unmounts. |

What the view shows:
- **Honesty tier.** read or measured: solid outline. detected: dashed outline, amber below 60% confidence. estimated: hatched. cannot determine: a card with the reason, and the drawing stays visible.
- **Counts.** The evidence is numbered on the drawing, using the backend's own labels when they are short.
- **BIM answers.** These count the whole model, so the badge says e.g. "14 of 119 shown on this sheet". Nobody hunts for doors that are on another storey.
- **Click.** Zooms to the item and shows the crop from the original, plus ID, position (in px, or metres on CAD sheets) and confidence.
- **3D.** The same camera tilts while walls rise out of the drawing. The wall tops are the drawing itself, so at height 0 the view is identical to 2D, and going back is exact. Answer items grow translucent volumes and their numbers float on pins. A note on screen says heights are illustrative. Wall sources:
  - Swiss sheets: the DXF `WALL` layer (exact).
  - BIM plans: `IfcWall` / `IfcWallStandardCase` shapes in the SVG; height assumes a 1:100 sheet.
  - Scans: walls found in the image (`src/walls.ts`). Dark pixels, a morphological opening that drops text and thin lines, then rectangles.
  - PDF pages get no walls. The image path works on full A1/A2 sheets too (the filter is sized from the sheet's own hairlines; 67-138 megapixel PNGs are decoded downscaled).
  - Door openings come from the sources themselves, so walls already have gaps there.

## Checking an answer (built-in tools)

Modelled on takeoff and BIM viewers (Bluebeam markups list and measure, Autodesk Measure and isolate, xeokit section planes, Matterport dollhouse), aimed at Stoa's "verify in seconds":

| Control | What it does |
|---|---|
| **‹ ›** in the answer card, or **← →** | Step through the evidence one item at a time: zooms to it and shows the crop of the original (e.g. "4 of 13") |
| **Click** an item / **double-click** anywhere | Zoom to the item / zoom in towards that spot |
| **F** | Frame the selection (or the whole answer) again |
| **Original** (hold), or hold **Space** | Hide every highlight and show the untouched drawing |
| **Measure**, or **M** | Click points to measure lengths; click the first point again to close an area. Points snap to the drawing's corners. Units come from the sheet: metres on CAD sheets, paper millimetres on BIM plans, pixels when there is no scale. The readout says which. **Esc** clears. |
| **Legend rows** | Switch a category of a breakdown answer on or off |
| **Walls** slider (3D) | Cut the walls lower to see into the rooms (heights are illustrative) |
| **Reset view**, **Esc** | Frame the sheet, clear the selection |

Keys are ignored while typing in the host page's inputs.

## Optional evidence fields (for the judges' harder questions)

The view reads `/api/ask` as it is today. These optional fields let the backend show multi-category and linear answers. All of them are optional and ignored if absent, and the view never computes a value itself:

```jsonc
{"answer": 348.47, "unit": "m2",
 "breakdown": [{"group": "Dormitory", "value": 18665, "unit": "ft2", "count": 144}],   // legend subtotals, in this order
 "evidence": [
   {"id": "r12", "shape": "polygon", "pts": [...], "group": "Dormitory", "value": 24.3, "unit": "m2"},   // colour per group
   {"id": "b3",  "shape": "polyline", "pts": [...], "group": "Classroom", "value": 31.2, "unit": "m"},   // baseboard / any line
   {"id": "s1",  "shape": "polygon", "pts": [...], "group": "300 slab, D16 @ 300", "thickness_m": 0.30}, // slab at printed thickness
   {"id": "p7",  "shape": "polyline", "pts": [...], "group": "8\" SS", "diameter_m": 0.2032}            // pipe at printed size
 ]}
```

| Field | 2D | 3D |
|---|---|---|
| `group` | colour per group, legend (with `breakdown` subtotals, else counts) | volumes coloured per group |
| `value` + `unit` | value tag per item (shown on hover when there are more than 12 items) | same |
| `shape: "polyline"` | thick line, clickable | baseboard strip at floor level, or a tube when `diameter_m` is given |
| `thickness_m` | as polygon | solid plate at that thickness (needs a sheet scale) |
| length answers (`unit` m/ft) with polygon evidence (e.g. room perimeters) | outlines, no fill | baseboard strips along the outline |

To try them: `/?d=site11692_bldg18240_floor48820&synthetic=areas|baseboards|slabs|pipes` (harness only; synthetic values from that sheet's real room polygons).

## Serving

Either one line in `demo/server.py` (in `do_GET`, next to the other routes):

```python
if u.path == "/viz/drawview.js":
    return self._send(200, open(os.path.join(ROOT, "viz", "dist", "drawview.js"), "rb").read(), "text/javascript; charset=utf-8")
```

or, without touching the server, a second local port: `python3 -m http.server 5174 -d viz/dist`, and
`<script src="http://localhost:5174/drawview.js">`. A classic script needs no CORS, and the bundle calls `/api/*` on the page's own origin.

## Develop

```bash
cd viz && npm install
npx vite --port 5180          # harness: http://localhost:5180/ (replays fixtures), /?live=1 (real demo on :8765 via proxy)
npx vite build                # -> dist/drawview.js
npx tsc -p .                  # typecheck
python3 tests/matrix.py http://localhost:5180 /tmp/shots    # every fixture x {plain, answer, selected, cannot, 3D}: screenshots + console errors
python3 tests/toggle.py 'http://localhost:5180/?d=colorful-11397&q=0' /tmp/shots   # 2D -> 3D -> 2D frames; the round trip must match
python3 tests/interact.py http://localhost:5180 && python3 tests/features.py http://localhost:5180 /tmp/shots   # clicks, keys, tools
# tests/embed.html: the built bundle on a plain page against the live server (?path=...&q=...)
```

`fixtures/` holds real responses from the demo server for its 7 sample drawings, captured with `DRAWQA_LLM=0` by `tools/capture_fixtures.py`.

## Files

`src/index.tsx` public API · `src/adapter.ts` demo JSON to sheet pixels (image px / Swiss DXF metres, y-flip of `/api/sheet` / BIM SVG guid outlines) · `src/View.tsx` badge, detail card, tools · `src/Scene.tsx` three.js scene (camera, walls, lift) · `src/walls.ts` walls from scan images · `src/harness.ts` dev page (`?mode=3d`).
