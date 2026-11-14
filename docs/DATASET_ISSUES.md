# Where the dataset's answers (and some drawings) are wrong, and what we do about it

Read this before trusting a score. Stoa said it themselves: "humans made the original dataset, humans can make
mistakes of certain margin". Some of the answer key is also machine-made from file data, and those mistakes are
systematic. Our rule (user decision, 15 Nov): **answers say what a person sees on the drawing**, never what a file
stores invisibly. Where the key disagrees with the drawing, we keep the original answer untouched and record a
correction with its reason in `data/answer_corrections.jsonl`; `drawqa/eval.py` applies it and prints how many it used.

## 1. Swiss Dwellings: door and window counts include copies stored twice (systematic, 61 of 65 plans)

**What:** the Swiss DXFs often store the same door or window twice, exactly on top of itself (same rectangle to the
millimetre, same swing arc). On the picture and the PDF it is one door. The dataset's questions ("How many doors
(interior doors and entrance doors together) are drawn on this floor plan?") were answered by counting DXF elements,
copies included.

**How big:** on the 65 preview plans, the key totals 1,777 doors where 1,501 are drawn, and 1,040 windows where 1,012
are drawn. 61 of 65 plans are affected; the key equals the drawn door count on only 4 plans. Of the 276 extra door
elements on repeated openings, 273 are the same leaf drawn twice (identical arc) and only 3 are two different leaves
sharing one opening (a double door). Example: `site1000_bldg2082_floor9385`: key 36 doors and 28 windows; drawn 29
and 26 (checked by eye on the image, and by distinct geometry in the DXF).

**History:** an earlier session noticed this ("the answer key counts CAD rectangles", HANDOFF.md) and chose to match
the key: `snap_to_linework` deliberately counted overlapping rectangles twice on vector PDFs. Reversed on 15 Nov:
- `drawqa/swiss.py` counts an element stored twice at the identical box once (`sheet["duplicates_dropped"]`,
  `sheet["duplicate_copies"]` keep what was dropped); answers do not mention the hidden copies.
- `drawqa/convert.py` `snap_to_linework` counts a path drawn twice once (both the style route and per detection).
- `data/answer_corrections.jsonl` (150 rows, from `tools/make_answer_corrections.py`): a question is corrected only
  when its answer equals the count WITH the copies and differs from the count without them.
- Swiss DXF eval stays 2,326/2,358 = 98.6% against the corrected key (doors and windows 130/130). On the DXF route this
  check is circular (same rule); the picture route (`DRAWQA_SWISS=pdf`) is the independent test.

**Training data too:** `tools/swiss_labels.py` builds the detector's Swiss training labels through `swiss.load`, so
about 10% of the Swiss door/window training boxes were exact duplicates. Harmless for counting, but with the fix the
next training run gets clean labels automatically.

## 2. Swiss renders carry a legend with sample symbols that are not in the DXF

The PNG/PDF renders have a legend strip ("Legende": WC, Lavabo, Badewanne, Dusche, Türe, Fenster) with one sample of
each symbol. A detector reading the picture finds the sample door as a door (site1000: 30 instead of 29). Now
`drawqa/convert.py` `legend_boxes()` / `drop_legend_symbols()` find a legend heading (Legend, Legende, Légende, Legenda,
Leyenda, Zeichenerklärung) in the sheet's text, take the ruled panel around it (or a small area by the heading if there
is no panel or it would cover more than a third of the sheet), and leave detector symbols inside it out of every count;
the answer says "Not counted: 1 door symbol(s) drawn in the legend". Vector-PDF door arcs already had such a rule.

## 3. Swiss sheets come at two scales

The renders are A3 at 150 dpi (2480 px wide) at **1:200 or 1:100** (the title block says "Massstab 1:N (A3)"):
29.5 or 59.0 px per metre. Our engine's scale estimate was 4-6% off on them (0.0358 m/px against the true 0.0339 on
site1000), which makes areas about 10% off. Reading the printed scale would fix it (not done yet).

## 4. Swiss DXF details that break naive tools

- Some buildings sit at a small angle in the DXF (about 2 degrees); the render turns them to the page axes.
- Doors drawn back to back at a wall corner touch each other: grouping "touching lines" merges two doors into one.
- A double door is two arcs hinged exactly two radii apart on one opening: one door, not two.
The training labels handle all three correctly (one door = one closed opening rectangle, `swiss.load`); quick
answer keys built from line groups do not.

## 5. Swiss showers: the key is right, our rule was too narrow (not a data error)

The shower questions the engine got wrong (14 of 42) were ours: showers are recognised as empty square trays, and the
rule only allowed up to 1.05 m and near-square. Widened to 1.25 m and sides within 1.5x, with a guard that an empty box
inside a room named as a kitchen (Küche, Cuisine, Cucina, Kitchen) is an appliance, not a shower: 28/42 -> 34/42. One
plan (site2294) had been "right" by luck: a counter piece in the kitchen was counted as its one shower.

## 6. CubiCasa (Finnish scans): human annotation, no systematic error found

- Doors and windows: on 5 held-out plans every door and window label was right (104/104 against our detector).
- Double annotations: 3 of about 1,000 objects on 20 plans (appliance 1/92, cabinet 2/102). Not systematic.
- Conventions, not errors: a door's box is the opening in the wall (not the swing); closets and cabinets are labelled
  per module. A different convention (e.g. Opus boxing the swing) scores as "missed + extra" for the same door.
- Plans without a printed scale: the door-width scale estimate can be 20-27% off (a coloured plan with no scale); use
  the median of several standard sizes, and say "estimated".

## 7. IFC models: no duplicates

None of the 9 IFC models (ifc_models, ifc_bench) stores a door or window twice at the same place.

## 8. Drawings that contradict themselves (traps, not dataset errors)

- The user's data-centre schematic labels the overall length 78,000 mm but draws nine 7,800 mm bays (70,200 mm), and
  its grid skips number 10. Read the label, but say that it does not add up.
- The US dormitory sheet's area table is gross floor area; outlines inside the walls are net (17-19% less). Filling
  the interior walls closes the gap to -8%; the rest is exterior walls and cores. The scale was checked against the
  drawn scale bar (0.3%).

## 9. Real-world CAD files

- `opening_design/.../Site plan.dxf`: everything is inside blocks (9 INSERTs); a reader that does not expand blocks
  counts nothing. `drawqa/cad.py` draws it to a page (blocks expanded) instead.
- `opening_design/.../A-101 - FLOOR PLAN - WAREHOUSE.dwg`: one entity sits kilometres from the building (extents
  219,428 x 130,399 inches), so a page fitted to the extents shows the plan as a dot. `cad.py` now fits the page to the
  1st-99th percentile of entity positions (widened by half) and leaves the strays out of the drawing.
