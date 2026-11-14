# Brief: plan reader for a piping / equipment plan

You read a mechanical piping plan for DrawQA, which answers questions about construction drawings and says for every
answer how it was obtained. Questions about this kind of sheet are about the equipment and the building: how many
pumps, which tags, pipe sizes, the scale, which rooms there are and how big. Your output is a model in plan_tool.py's
format (read its docstring), written to model_final.json in your work folder.

In your work folder:
- start_model.json: room outlines our wall model traced ("rooms", "source": "wall model"; no names, some may be wrong).
  It has no doors, windows or fixtures: this sheet is not one our symbol detector was trained on.
- ocr_text.json: the words our OCR read, with pixel boxes. Some are misread: hints, the drawing decides.

Your job:
1. rooms: every enclosed space of the building (the main pump room, toilet, other rooms). Name: the printed name if the
   sheet prints one, else null. Type: room, wc_room, utility, storage, entry, or other_room; a room with no printed name
   is typed from what is drawn in it. Fix, merge, split, add or delete candidates as needed; polygons follow the inner
   faces of the walls.
2. fixtures: one entry per tagged piece of equipment: the tag printed with a leader to a symbol (FP-1, FSCV-3, CV-2,
   FSI-4, FSR-1, PIT 3, DPT-1, ...). "id": the tag exactly as printed; "type": the tag's prefix (FP, FSCV, CV, ...);
   "bbox": the symbol the tag points to, not the text. Pipe-support marks (PS-n) are a type tag reused at many places:
   one fixture per place a PS tag points to, id "PS-n@k" (k = 1, 2, ...). Also add the toilet room's WC and sink as
   fixtures of type wc and sink.
3. m_per_px and scale_source: from the graphic scale bar (measure its ticks in pixels) and check against the printed
   scale and the paper size in the title block; say what you used.
4. floors: one bbox around the plan drawing (not the notes, title block or grid labels).

Tools: plan_tool.py (in the same folder as this brief), run with /Users/alexp/Documents/junction/.venv/bin/python.
- `grid <image> <out.png> x0 y0 x1 y1`: a zoomed region with a pixel grid. The sheet is 10200 x 6600 px: always crop.
- `check <image> <model.json> <outdir> --n 3`: overlay tiles of your model; `lint <model.json> --ocr ocr_text.json`.
View the PNGs with Read. Work region by region; check, fix; three rounds at most.

Rules:
- Work blind: don't open files named *KEY* or *ENGINE*, anything under planloop/gt/, planloop/fixkeys*/,
  /Users/alexp/Documents/junction/data/, or any planloop folder except your own work folder.
- No internet. Write only in your work folder.

Final report: room count by type, fixture count by type, the scale and how you got it, and anything you were unsure of.
