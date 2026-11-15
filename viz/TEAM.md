# DrawView for teammates: what it does, what it needs from you, how not to collide

The drawing view (`viz/`, owner: oliraff) is the left side of `ui/` and the classic demo. It shows every answer on
the drawing itself, so a judge can check it in seconds. This page is the short version. Details: `README.md`
(API, options), `CHANGELOG.md` (what changed when, plus an "Already built" table).

## 1. Who edits what (no-conflict rules)

| Area | Owner | Rule |
|---|---|---|
| `viz/` (source, tests, `viz/dist/drawview.js`) | oliraff | Please don't edit. Ask on issue #1 or open an issue; I turn it around fast. `dist/drawview.js` is a build output: never hand-edit it. |
| `ui/`, `tools/ui_server.py` | kokorosf | I never edit these. `ui/` uses the view only through the public API below. |
| `drawqa/`, `demo/`, `gold/`, `tools/` (rest) | Sessions A/B | I never edit these. I only read your JSON. |

The view lands on `main` through PRs from the `viz` branch, only ever touching `viz/`. Every PR is tested
inside `ui/` before it is opened.

## 2. What the view already does: please reuse, don't rebuild

- **Every answer drawn on the sheet,** styled by honesty tier:
  - read and measured: solid;
  - detected: dashed, amber when unsure;
  - estimated: hatched;
  - cannot determine: a card with the backend's reason.
- **Counted symbols light up as the whole object.** Doors, WCs, basins, tubs, showers and windows light up as one filled shape with a numbered pin and a class glyph. BIM symbols use their own SVG paths.
- **Checking tools:**
  - stepper ‹ › and ← →, with a crop of the original per item;
  - Measure (scale source stated);
  - hold Original / Space for the untouched sheet;
  - double-click zoom, F to refit.
- **Inspect any object without a question:** hover shows what it is (and whether it was detected, read or from the IFC model). Click zooms in with a card. Blue means inspecting; orange is reserved for answers.
- **Clean view:** the plan redrawn from what was detected, with a swipe divider against the original.
- **3D:**
  - walls, floors per room type (oak, stone, tile, plus a tiled band on wet-room walls), and a real model for every fixture class you recognise (WC, basin, tub, shower, kitchen, sauna, washer, stairs, shaft, cabinets, door leaves, windows with frames, BIM stairs, railings and furniture). New class names: tell me on #1 and I'll add a model;
  - a cutaway: walls on your side drop low;
  - the counted object's model lights up orange with a floor glow, and the selected one gets a callout (name, n of N, id, source);
  - wall cut slider, and a path-traced Photo mode;
  - heights are labelled illustrative and never used for answers.
- **Sheets with several drawings:** says which drawing the evidence comes from (or the split), and a Drawings picker frames each one.
- **Safety nets:**
  - BIM answers counting the whole model say "N of M on this sheet";
  - a count citing the same outline twice gets a note;
  - evidence off the page is never drawn;
  - rotated PDF pages are detected and corrected (see 5).

## 3. Using it from a page (`ui/` already does this)

```js
const view = DrawView.mount(el)          // once; el needs a height
await view.open(path)                    // fetches /api/render, /api/plan, /api/sheet (one after the other)
view.showAnswer(askJSON, question)       // after /api/ask
view.select(evidenceId); view.onSelect(el => …)
view.clear(); view.reset(); view.setMode('2d' | '3d'); view.setClean(on, swipe?)
```

## 4. What the view reads from your JSON (please keep these stable)

All positions are in the **page-image pixels of `/page`** (y down). On Swiss vector sheets they are the render's metres, with `y_flip`.

| Field | What the view does with it |
|---|---|
| `/api/render` `type`, `width`, `height`, `url`, `svg`, `items` | Draws the sheet. `width`/`height` must equal the `/page` image size. |
| `/api/ask` `evidence[]`: `shape` `polygon`/`box`/`guid`/`polyline`, `pts`/`bbox`, `label`, `id` | The highlights. **Use the sheet's own ids as evidence ids** (`door21`, `fx75`, `arc2`, IFC guids): the view looks them up in `/api/sheet` to know the class (WC, door, …). |
| `/api/ask` `honesty.tier`, `basis`, `explanation`, `unit` | Style, chips, and the "can't answer" reason. Put the real reason **first**. A trailing "language model not available" note is hidden, and the reason before it is shown. |
| optional `group`, `value`, `unit`, `breakdown[]`, `thickness_m`, `diameter_m` | Coloured groups with a legend, per-item values, slab thickness and pipe size in 3D (README "Optional evidence fields"). |
| `/api/sheet` `rooms[]`: `name`, `number`, `area_m2`, `area_source`, `polygon`/`bbox`, `cls` | Clean-view labels, 3D floor materials. |
| `/api/sheet` `openings[]`, `fixtures[]`: `id`, `cls`, `polygon`/`bbox` | 3D windows, door leaves and fixture models; class of evidence ids. |
| `/api/sheet` `texts[]` (`text`, `bbox`, `bold`), `scale.candidates[]` (`text`, `bbox`) | Drawing titles and scales on multi-drawing sheets; rotated-page detection. |
| `/api/sheet` `walls[]` | 3D walls (scans without walls get them from the image). |
| `/api/plan` `items[]`: `type`, `bbox`/`pts`, `conf` | The faint "found on this sheet" layer, detector confidence, scan floors as drawings. |

If you change one of these, a heads-up on issue #1 is enough. I adapt the view.

## 5. Open asks for the backend (found while building the view)

1. **Rotated PDF pages:** positions come back in the unturned page frame (A477, Springhaven, 1810 S Park St). Fix: transform PyMuPDF coordinates with `page.rotation_matrix`. The view corrects this on its own for now, but other consumers (classic page, gold scorer) don't.
2. **"What is the ceiling height?"** offline now says "language model on Modal not available". Earlier it gave the deterministic "cannot determine: floor plans have no heights".
3. **A lock around IFC model loading** in `drawqa/ifc.py`: parallel requests can still crash ifcopenshell.
4. **Nice to have:**
   - `page` on answers from multi-page PDFs;
   - text-box evidence for "read" answers that cite none (Cherokee Path 218 / 906 SF, A477 balcony);
   - `group` / `breakdown` on multi-category answers.

## 6. Testing your change against the view (2 minutes)

```bash
python tools/ui_server.py --port 8770 --viz <path to viz/dist/drawview.js>   # your server + the view
python viz/tests/teampage.py http://localhost:8770 /tmp/out    # samples + gold drawing in ui/, 0 console errors
python viz/tests/demo.py http://localhost:8770 /tmp/out        # 4 offline-safe questions end to end
```

In the view's dev harness, `?path=<server path>&ask=<question>` opens any drawing your server knows and asks it live.
