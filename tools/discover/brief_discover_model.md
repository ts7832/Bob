# Brief: what is drawn on this sheet, starting from our trained models (open discovery, on a budget)

You get one construction drawing. Nobody tells you what to look for. Find every distinct KIND of thing drawn on the
sheet (building parts, equipment, fittings, furniture, lines and networks, symbols) that a person asking questions
about the drawing might want counted, measured or located. Make up a short class name for each kind yourself.
Leave out pure annotation (dimension lines, leader arrows, text, title block, grid bubbles) unless it marks a
physical thing.

The bar is precision: every item's geometry must sit exactly on the drawn object, and every count must be exact.
Do whatever it takes: zoom in, measure with the tools, write your own small scripts (PIL, numpy, cv2 are installed),
and check every item on a full-resolution crop. Never type coordinates read off a downscaled view as final.

## Tools (ptool.py, in the folder of this brief; run with /Users/alexp/Documents/junction/.venv/bin/python)
Read its docstring first. All coordinates are full-resolution sheet pixels.
- `find_like <image> x0 y0 x1 y1 out.json --sheet out.png [--rot] [--min 0.7] [--in X0 Y0 X1 Y1]`: box ONE clean
  example of a repeated symbol; get every look-alike with a score and a numbered contact sheet. Accept or reject each
  hit by looking at the sheet; lower --min to catch more, add --rot for rotated or mirrored copies. Run it again with
  a second example if one variant is drawn differently.
- `snap_box <image> x0 y0 x1 y1`: a rough box -> the tight box of the object's ink.
- `trace <image> x y x2 y2 [--mpp M] [--out f.json]`: anything linear drawn as one line or two parallel lines (pipes,
  ducts, rails, cables, walls): follows it from (x, y) towards (x2, y2) through bends, keeps curves, reports width
  and why it stopped. Start new traces where it stops (a tee, a size change, a riser) and join them yourself.
- `snap_line <image> x0 y0 x1 y1 [--mpp M]`: one straight segment -> the exact drawn line or line pair, with width.
- `fill <image> x y [--close N]`: an enclosed area -> its exact outline and area.
- `crops <image> items.json out.png`: every item drawn on a full-resolution crop around it, numbered. Use it to verify.
- `grid <image> out.png x0 y0 x1 y1` is in plan_tool.py (same folder): a zoomed crop with a pixel grid, for finding
  where to start.

## Start layer from our trained models
start_layer.json in your work folder holds what our trained models already found, in the output format below:
doors, windows and fixtures (WC, sink, shower, bathtub, stove, sauna bench) from a trained symbol detector, walls
(centre line, outline and thickness) and room outlines from a wall model (with printed names found inside them), and
the sheet's scale. start_overlay.png shows them.
These models are accurate on their classes: keep their items as they are and do NOT search for those classes again.
Change a model item's class only with drawn evidence (a door swing, a window frame symbol, a printed label), never by
colour or by a legend you assume: if you are unsure, keep the model's class and note the doubt in "uncertain".
Look at start_overlay.png once; remove an item only if it is plainly wrong, add one only if it is plainly missing.
Every model item with a score below 0.5 goes on one `crops` sheet: keep it only if its symbol is really drawn there (a
"window" on a plain wall or on crossing pipes is plainly wrong).
Classes "door candidate" and "window candidate" (if present) are places where the detector saw a door or window but
scored it below its cutoff: put them on that same `crops` sheet. Move each one where a door or window really is drawn
into "door" or "window", with "checked": "candidate confirmed on crop"; drop the rest. Never output a candidate class.
Doors are drawn in several ways, all of them doors: a leaf with a swing arc; a sliding door (one or two straight
leaves lying along the wall opening, often offset, sometimes with a short arrow); a folding door (a zigzag or accordion
line across the opening); a roll-up or garage door (a dashed or thin line across a wide opening). A window is a frame
of thin parallel lines inside the wall band. Confirm a candidate when such a symbol sits in a wall opening. Check every room outline of the wall model on the
overlay too: a room must be bounded by drawn walls on most of its sides; an outline drawn around text, an aisle or a
group of furniture inside a bigger room is plainly wrong (drop it, and outline the real room on its walls if missing).
If the start layer has no rooms (on large sheets the wall model reads only walls), outline every room yourself: one
polygon per room on its inner wall faces (`fill` at a point inside it, `--close` to bridge door openings; several rooms
in one script), named from its printed label.
A ROOM is a whole space a person would name (a dorm room, a lounge, a corridor, a laundry): everything between its
walls, furniture and fixtures included. An enclosed bathroom, closet or entry inside it stays part of that room; if you
outline it too, put it in a separate class "sub_space" (with the id of the room it is in), never in "room", so the
room count stays the count of rooms. Room outlines follow the walls: on a drawing laid out on axes their edges are
horizontal and vertical, so a long diagonal edge means the outline is wrong.
An outline that follows hatching, posts or roof lines instead of walls (ragged, many corners) is plainly wrong: drop it,
or replace it with a clean polygon on the real edges.
Spend your budget on everything else drawn on the sheet: the kinds of things the models do not know.
Your discovered.json must contain the start layer's classes (as kept) plus your own classes; give each class a
"source" ("trained symbol detector", "wall and room-outline model", or "Claude").

## Budget
About 25 tool calls in total. Spend them on coverage first, then precision: survey the sheet in 4-6 zoomed regions,
use `find_like` for repeated symbols (one call per symbol, with --rot), put many items into one script instead of
one call per item, and check all classes on 2-3 `crops` sheets at the end rather than one sheet per class.

## Before you finish
Run `ptool.py lint_layer discovered.json <image>`: it flags probable noise and mistakes in every class, the start
layer's included (ragged outlines, odd sizes, duplicates, items outside the image). Resolve every flag: fix the item on
the real edges, or drop it. Noise must not stay in the result.

## Procedure
1. Survey: view the sheet at low resolution and zoomed crops; list the kinds of things you see.
2. For each class, choose the geometry that fits it: "box" for symbols and objects, "polyline" for linear things,
   "polygon" for areas, "point" for dots. Build the items with the tool that fits (find_like for repeated symbols,
   trace for linear things, fill for areas, snap_box for one-off objects) or with your own script.
   Count what is drawn: where identical units are drawn side by side (cells of a rack row, seats, lockers, cabinet
   modules), each drawn unit is one item; add the row or group as a class of its own if that is useful too.
3. Verify: `crops` for every class. Fix or remove every item that does not sit exactly on its object; add what is
   missing. Repeat until the crops are clean. Count = number of verified items.

## Output: discovered.json in your work folder
The user watches discovered.json live while you work. Write it early and keep it current: first right after you have
checked the start layer (its classes as kept), then again each time you have built a class (the same script that
builds the class merges it into the file: no extra tool calls). A class you have not verified yet may carry
"verified": "not yet". The file must always be valid JSON in the format below.
{"sheet": "one line: what this drawing is",
 "classes": [{"name": "...", "what": "one sentence", "how_drawn": "how you recognise it",
   "geometry": "box|polyline|polygon|point", "count": n, "method": "which tool or script made the items",
   "verified": "how many items you checked on crops and what you fixed",
   "items": [{"bbox": [x0, y0, x1, y1]} or {"polyline": [[x, y], ...]} or {"polygon": [[x, y], ...]} or {"point": [x, y]},
             plus any value you read, e.g. "tag", "size", "label"]}]}
Give every item of every class (no samples).

## Rules
- Work blind: open only the image, the tools and this brief, and your own work folder (nothing under
  /Users/alexp/Documents/junction/data/ or junction-dataset-preview/ except the image itself).
- No internet. Write only in your work folder.

Final report: a table (class, count, geometry, method), then what you are least sure of.
