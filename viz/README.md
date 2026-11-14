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
  - PDF pages get no walls.
  - Door openings come from the sources themselves, so walls already have gaps there.

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
# tests/embed.html: the built bundle on a plain page against the live server (?path=...&q=...)
```

`fixtures/` holds real responses from the demo server for its 7 sample drawings, captured with `DRAWQA_LLM=0` by `tools/capture_fixtures.py`.

## Files

`src/index.tsx` public API · `src/adapter.ts` demo JSON to sheet pixels (image px / Swiss DXF metres, y-flip of `/api/sheet` / BIM SVG guid outlines) · `src/View.tsx` badge, detail card, tools · `src/Scene.tsx` three.js scene (camera, walls, lift) · `src/walls.ts` walls from scan images · `src/harness.ts` dev page (`?mode=3d`).
