# Brief: plan reader, starting from the wall model's outlines (v3, English sheets)

You read the floor-plan layer of a drawing for DrawQA, which answers questions about construction drawings. Two trained
models have already done part of the work. Both are in start_model.json in your work folder:
- the detector found doors, windows and fixtures. Not your job: copy them unchanged.
- the wall model traced room outlines from the walls ("rooms", each with "source": "wall model"). These outlines are
  usually very precise (median area error about 3%), but they have no names, most have no type, and the wall model
  sometimes merges two rooms, splits one room, misses a room, or outlines something that is not a room.

Your job: turn the candidates into the true room list.
1. Every candidate that is a real room: set "name" to the printed name inside it (copied as written) and "type". Keep its
   polygon exactly as it is unless it is clearly wrong.
2. One candidate covering two rooms (a wall or two printed names inside it): split it into two polygons along that wall.
   A combined name printed in one space ("Kitchen / Dining") names an open plan of two rooms: split it too, one
   polygon per name. ("Servery and Furniture Store" is one room with a long name.)
3. Two candidates that are one room (no wall between them, one name): merge them into one polygon.
4. A room with no candidate: add it, with a polygon along the inner faces of its walls (find the corners with `grid`).
   A small named store, toilet or plant room is a room. Unlabelled built-in cupboards and furniture are NOT rooms.
5. A candidate that is not a room (a wall sliver, furniture, an unlabelled cupboard, a shaft, the outside, a courtyard,
   or a partly drawn neighbouring unit cut by the sheet edge): delete it.
   An unnamed candidate is kept only when it is clearly a space of this building: a hall or passage (type entry), or an
   enclosed space with a door. The wall model often cuts one room in two at a furniture line, a beam or a wide opening:
   an unnamed piece with no wall between it and a named room is part of that room (merge), unless it is a passage
   leading on to several rooms.
6. floors: one bbox per separate floor-plan drawing on the sheet. Keep the start one if it is right.
7. m_per_px and scale_source: only if the sheet gives a scale (dimension lines with numbers, a scale bar, or a printed
   room area you can match to its outline); otherwise null.

Types, from the printed name (English plans; copy the name exactly as printed, e.g. "Secondary Classroom (Severe Needs)"):
- room: classrooms, group rooms, offices, library, hall, meeting / interview / therapy / medical rooms, staff
  workroom, fitness room, reprographics, and any named room that fits nothing else.
- entry: corridor, lobby, lift landing, reception, entrance lobby, enclosed walkway, an unnamed hall or passage.
- storage: store, secure store, furniture store; utility: plant room, laundry, server room.
- wc_room: toilet, accessible toilet, WC; bathroom: changing (and showers), hygiene room, accessible changing.
- kitchen: kitchen, servery; other_room: a stair or lift with a printed name.
- Another unnamed enclosed space: room.
A name printed twice in one space (two "Corridor" labels along one corridor) is still one room.

Tools: plan_tool.py (in the same folder as this brief), run with /Users/alexp/Documents/junction/.venv/bin/python. Read
its docstring for the format.
- `check <image> <model.json> <outdir> --n 2`: zoomed overlay tiles with a pixel grid; view the PNGs with Read.
- `grid` and `render` help with detail.

Also in your work folder: ocr_text.json, the words our OCR read on the sheet with their pixel boxes. Upside-down
labels can come out reversed ("HW" = MH, "HO" = OH) and some words are misread: they are hints, the drawing decides.

Procedure:
1. Run `lint start_model.json --ocr ocr_text.json` (plan_tool.py) and `check` on start_model.json.
2. Write model_final.json. Resolve every lint item:
   - SEVERAL WORDS: two room names in one outline mean two rooms: split it. An open plan (OH + RT + K, no wall
     between) is split at the boundary the drawing suggests: the end of the kitchen counter, a wall stub, a change of
     floor pattern, or else halfway between the labels.
   - WORD IN NO ROOM: if it is a room name, that room is missing: add it.
   - OVERLAP: fix the polygons so rooms do not overlap.
   - NO NAME: read the label from the drawing. NO NAME, NO DOOR: decide merge, delete or keep (see 5).
3. Run `lint` and `check` on model_final.json; fix what remains. Four rounds at most.

This is a large sheet (about 14000 x 9900 px) with about 80 rooms, and the wall model read it whole, so it found only
about half of them as outlines: expect many WORD IN NO ROOM items that are real missing rooms (small stores, toilets,
group rooms). Add each one with a polygon read off a zoomed `grid` view. Work region by region (west teaching block,
walkway, east hall and admin block); `check --n 4` gives finer tiles.

Rules:
- Work blind: don't open files named *KEY* or *ENGINE*, anything under planloop/fixkeys*/ or planloop/gt/,
  junction-dataset-preview/questions.jsonl, anything under /Users/alexp/Documents/junction/data/, or any planloop
  folder except your own work folder.
- No internet. Write only in your work folder.

Final report: one line: the number of rooms by type.
