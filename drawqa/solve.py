"""Single entry point: solve(path, question) -> answer + evidence + explanation."""
import os, re
from functools import lru_cache

from . import router, query, swiss, scan

DATASET_SOURCES = ("swiss_dwellings", "cubicasa5k", "ifc_models", "ifc_bench")


def source_of(path):
    return next((s for s in DATASET_SOURCES if f"/{s}/" in path), "unknown")


# Questions in the dataset come grouped by drawing (320 drawings, 321 changes of drawing in file order), so a small
# cache loses nothing. It was 64: a whole-source run then kept 64 converted sheets alive, and on this 8 GB machine swap
# reached 7.7 of 8 GB on 2026-11-14 at 14:2x (the morning's kernel panic was the same condition).
@lru_cache(maxsize=4)
def load_sheet(path, generic=False):
    """generic=True: treat the file as unknown (no dataset shortcuts such as a DXF sibling or stored predictions)."""
    if generic:
        from . import convert
        return convert.convert(path)
    src = source_of(path)
    if src == "swiss_dwellings" and os.environ.get("DRAWQA_SWISS") == "pdf":  # read the PDF itself, no CAD file
        from . import convert
        return convert.convert(os.path.splitext(path)[0] + ".pdf")
    if src == "swiss_dwellings":
        dxf = os.path.splitext(path)[0] + ".dxf"  # same name = same drawing (README)
        sheet = swiss.load(dxf)
        sheet["render_source"] = dxf
        return sheet
    if src == "cubicasa5k" and os.environ.get("DRAWQA_LIVE") == "1":  # run the detector on the image now
        from . import convert
        return convert.convert(path)
    if src == "cubicasa5k":
        return scan.load(path)
    if src in ("ifc_models", "ifc_bench"):
        from . import ifc  # heavy import, only when needed
        return ifc.load(path)
    from . import convert  # any other file: recognise it by content
    return convert.convert(path)


SYMBOL_INTENTS = ("count_openings", "count_fixtures")


def honesty(sheet, intent, res):
    """How the answer was obtained, in the terms the judges asked for.

    tier: read (printed on the sheet) | measured (exact geometry or model data) | detected (trained detector found
    the elements; each one is shown) | estimated (depends on a stated assumption) | cannot determine
    """
    if res.get("answer") is None:
        return dict(tier="cannot determine", deterministic=None, basis=res.get("explanation", "not supported for this drawing"))
    if res.get("assumed"):  # a number the drawing does not give (a room height): never better than estimated
        inner = honesty(sheet, intent, {k: v for k, v in res.items() if k != "assumed"})
        return dict(inner, tier="estimated", deterministic=False, basis=f"{res['assumed']}; the rest: {inner['tier']}, {inner['basis']}")
    st, it = sheet["source_type"], intent["intent"]
    scale = sheet.get("scale") or {}
    if it == "opening_width" and st != "ifc":
        used = res.get("elements_used", [])
        est = bool(scale.get("estimated"))
        sc = f"scale {scale.get('source')}" + (f" ({scale.get('length_error')})" if est and scale.get("length_error") else "")
        if st == "vector-dxf":
            return dict(tier="measured", deterministic=True, basis="symbol sizes in the CAD file's own units")
        if st == "vector-pdf" and used and all(e.get("exact") for e in used) and not est:
            return dict(tier="measured", deterministic=True,
                        basis=f"each {intent['cls']} matched to a rectangle in the PDF's own linework, measured at the {sc}")
        confs = [e.get("conf") for e in used if e.get("conf") is not None]
        return dict(tier="estimated" if est else "detected", deterministic=False, weakest=min(confs) if confs else None,
                    basis=f"{len(used)} {intent['cls']}(s) found by the trained detector, each width taken from its box; {sc}")
    if it == "scale":
        est = bool(res.get("scale_estimated"))
        return dict(tier="estimated" if est else "read", deterministic=not est, basis=scale.get("source") or "")
    by_detector = st in ("raster", "vector-pdf") and (it in SYMBOL_INTENTS or (st == "raster" and it in ("count_rooms_class", "count_floors")))
    if by_detector:
        confs = [e.get("conf") for e in res.get("elements_used", []) if e.get("conf") is not None]
        low = sum(1 for c in confs if c < 0.6)
        note = f"{len(confs)} element(s) found by the trained detector; {low} with confidence below 0.6" if confs else "no element found by the trained detector"
        exact = sum(1 for e in res.get("elements_used", []) if e.get("exact"))
        if exact:
            note += f"; {exact} matched to rectangles in the drawing's own linework (exact geometry)"
        out = dict(tier="detected", deterministic=False, basis=note, weakest=min(confs) if confs else None)
        fit = sheet.get("detector_fit") or {}
        if fit.get("level") in ("weak", "none"):  # say so when the drawing is unlike what the detector was trained on
            # a 0 is not a measurement when the detector recognises nothing on this drawing, or when it recognises
            # windows but not one door and doors are asked for. Other zeros on a weak drawing stay (on the 5 weak
            # preview scans, 32 zeros such as "0 saunas" are right), with the warning
            if res.get("answer") == 0 and (fit["level"] == "none" or (fit.get("missing") == "door" and intent.get("cls") == "door")):
                return dict(tier="cannot determine", deterministic=None, style_warning=fit["note"],
                            basis=f"the trained detector found none, but {fit['note']}")
            out["style_warning"] = fit["note"]
            out["basis"] = note + ". Warning: " + fit["note"]
        return out
    if res.get("unit") in ("m2", "m", "m3") and st in ("vector-pdf", "raster"):
        # a printed-area source carries its own check in brackets ("... (its outline gives 13.29 m2 ...)"): group without it
        short = lambda r: r.split(" (")[0] if r.startswith("printed area read by") else r
        src = " / ".join(sorted({short(r) for r in res.get("area_sources", []) if r})) or "geometry"
        if src.startswith("printed area read by"):  # the printed numbers themselves; outlines only confirm them
            used = res.get("elements_used", [])
            ok = sum(1 for r in used if "(agrees with" in (r.get("area_source") or ""))
            who = " and ".join(w for w in ("OCR", "a vision model") if w.replace("a ", "") in src)
            return dict(tier="read", deterministic=False,
                        basis=f"area printed in the room, read by {who} (a reader can misread a digit); {ok} of {len(used)} "
                              f"confirmed by the room's outline within 5% on lengths")
        if "segmentation model" in src:  # outlines drawn by a trained model: never exact, whatever the scale is
            confs = [c for c in (r.get("outline_conf", r.get("conf")) for r in res.get("elements_used", [])) if c is not None]
            est = bool(scale.get("estimated"))
            basis = (f"{src} ({len(confs)} shape(s)" + (f", weakest confidence {min(confs):.2f}" if confs else "") + f"); scale {scale.get('source')}"
                     + (f" ({scale.get('length_error')} on lengths" + (", so about twice that on areas)" if res.get("unit") == "m2" else ")") if est else ""))
            m = (sheet.get("outline_notes") or {}).get("measured") or {}
            c = m.get("room_area_error_corrected") or m.get("room_area_error") or {}
            if m.get("rooms_matched") and c.get("median") is not None and res.get("unit") == "m2":
                basis += (f". Measured on {m.get('plans')} held-out plans: this model outlines {m['rooms_matched'] * 100:.0f}% of rooms; "
                          f"an outlined room's area is typically off by {c['median'] * 100:.0f}% (one in ten by more than {c['p90'] * 100:.0f}%), before any scale error")
            return dict(tier="estimated" if est else "detected", deterministic=False, basis=basis, weakest=min(confs) if confs else None)
        if scale.get("estimated") and "printed label" not in src:
            return dict(tier="estimated", deterministic=False, basis=f"{src}; scale {scale.get('source')} ({scale.get('length_error')})")
        if src == "printed label":
            return dict(tier="read", deterministic=True, basis="area printed on the sheet next to the room number")
        return dict(tier="measured", deterministic=True, basis=f"{src}; scale {scale.get('source', 'from the file')}")
    if st == "ifc":
        return dict(tier="measured", deterministic=True, basis="read from the building model's own elements and quantities")
    if st == "vector-dxf":
        src = " / ".join(sorted({r for r in res.get("area_sources", []) if r}))
        return dict(tier="read" if src == "printed label" else "measured", deterministic=True, basis=src or "exact CAD geometry")
    return dict(tier="read" if st == "vector-pdf" else "measured", deterministic=True, basis="text and linework of the file")


@lru_cache(maxsize=32)
def _tables(path):
    """Schedules and other ruled tables on a PDF sheet (drawqa/schedule.py); [] for anything that is not a PDF."""
    try:
        with open(path, "rb") as f:
            if not f.read(5).startswith(b"%PDF"):
                return []
        from . import schedule
        return schedule.tables(path)[:8]
    except Exception:  # a sheet whose tables cannot be read is a sheet without table answers, not a failed question
        return []


def _ambiguous_count(question, tabs, intent):
    """A count the no-model path must not answer: it ties ONE cell value in ONE column to the question, which is wrong
    when the question names several values of that column ("AL-1 or AL-2", "types U06 and U44") or when the value
    also occurs in other columns ("all four walls finished with W1": four wall columns). Measured on the 19 counts
    of the preview's schedule questions: these two tests refuse exactly the three it got wrong."""
    if intent.get("intent") != "schedule_count" or intent.get("value") is None:
        return False
    from . import schedule
    t = tabs[intent["table"]]
    val, col = schedule._norm(str(intent["value"])), intent["column"]
    cell = lambda r, c: schedule._norm(str(r[c])) if c < len(r) else ""
    toks = set(schedule._tok(question))
    named = {cell(r, col) for r in t["rows"]} & toks
    elsewhere = any(cell(r, c) == val for r in t["rows"] for c in range(len(r)) if c != col)
    return len(named) > 1 or elsewhere


def table_answer(path, question, use_model=False):
    """A question answered from a table printed on the sheet (door schedule, finish schedule...), or None.

    Without a model only what can be tied to the table's own cell values is answered. With use_model, a language model
    (the service in tools/modal_reader.py) may write the query; the cells are still the sheet's own text and the
    lookup, count or sum is done by code, and the query is shown so it can be checked."""
    tabs = _tables(path)
    if not tabs:
        return None
    from . import schedule
    res = read = None
    if use_model:
        plan, note = schedule.plan(question, tabs)
        if plan:
            res = schedule.run(tabs, plan)
            q = res.get("query") or {}
            by_model = "no language model" not in note
            read = dict(by="language model" if by_model else "pattern",
                        note=f"{note}: {q.get('op')} on '{q.get('table')}' where {q.get('where')}"
                             + (f", column {', '.join(q['columns'])}" if q.get("columns") else ""))
    if res is None:
        intent = schedule.guess(question, tabs)
        if intent is None or _ambiguous_count(question, tabs, intent):
            return None
        res = schedule.answer(tabs, intent)
        read = dict(by="pattern", note="matched to the table's own values; no language model was used")
    if res.get("answer") is None:
        return None
    res.setdefault("evidence", [])
    res.setdefault("unit", None)
    who = "a language model" if read["by"] == "language model" else "matching the question to the table's own values"
    res.update(intent=dict(intent="schedule", **(res.get("query") or {})), method="table query", source_type="vector-pdf",
               question_read=read, confidence=0.9 if read["by"] == "language model" else 0.99,
               honesty=dict(tier="read", deterministic=True,
                            basis=(res.get("schedule_source") or "table rebuilt from the sheet's own words and ruled lines")
                                  + ". The cells are the sheet's own text and the arithmetic is done by code; which query answers "
                                    f"the question was decided by {who}" + (", so check the query shown." if read["by"] == "language model" else ".")))
    return res


def names_a_table(question, tabs, values=True):
    """True when the question is about a table on this sheet: it uses a table's title (at least two of its words and
    most of them), or it says schedule / scheduled / listed and the sheet has tables. Such a question is answered
    from the table, not by counting symbols: "how many doors are in the door schedule" is not a count of drawn doors."""
    import re
    q = set(re.findall(r"[a-z0-9]+", question.lower()))
    if tabs and re.search(r"\bschedul(e|es|ed)\b|\blisted\b", question, re.I):
        return True
    for t in tabs:
        words = {w for w in re.findall(r"[a-z]+", t["title"].lower()) if len(w) > 2}
        if len(words) >= 2 and len(q & words) >= 2 and len(q & words) >= 0.6 * len(words):
            return True
    return values and mentions_table_values(question, tabs)


GENERIC = {"room", "rooms", "door", "doors", "what", "which", "many", "total", "with", "from", "that", "this", "have", "there",
           "sheet", "drawing", "drawings", "plan", "plans", "number", "type", "types", "column", "notes", "note", "same", "each",
           "their", "they", "them", "these", "those", "into", "over", "under", "between", "level", "floor", "wall", "walls"}


def mentions_table_values(question, tabs):
    """True when the question uses the table's own contents without naming the table: a code that is a cell value
    ("door 115.A", "type B2", "finish F4", "ESDH3066 units"), or a column's header word together with a word that occurs
    in that table's cells ("area" + "locker" for "the combined area of the locker rooms")."""
    import re
    from . import schedule
    toks = set(schedule._tok(question))
    code = lambda w: bool(re.search(r"[a-z]", w) and re.search(r"\d", w))
    qwords = {w for w in re.findall(r"[a-z]+", question.lower()) if len(w) >= 4} - GENERIC
    for t in tabs:
        cells = [str(c) for r in t["rows"] for c in r if c]
        if {w for c in cells for w in schedule._tok(c) if code(w)} & {w for w in toks if code(w)}:
            return True
        head = {w for c in t.get("columns", []) for w in re.findall(r"[a-z]+", str(c).lower()) if len(w) >= 4} - {"column"}
        body = {w for c in cells for w in re.findall(r"[a-z]+", c.lower()) if len(w) >= 4} - head
        if qwords & head and qwords & body:
            return True
    return False


def _run(sheet, intent):
    if sheet["source_type"] == "ifc":
        from . import ifc
        return ifc.execute(sheet, intent)
    return query.execute(sheet, intent)


def _finish(sheet, intent, res, read_by):
    res.setdefault("evidence", [])
    res.setdefault("unit", None)
    res["intent"] = intent
    res["question_read"] = read_by
    res["source_type"] = sheet["source_type"]
    res["method"] = {"vector-dxf": "exact vector geometry", "vector-pdf": "PDF text and linework", "ifc": "BIM model query",
                     "raster": sheet.get("model", "detector")}.get(sheet["source_type"], "n/a")
    res["honesty"] = honesty(sheet, intent, res)
    if res["honesty"]["tier"] == "cannot determine" and res.get("answer") is not None:  # e.g. a 0 from an unfamiliar style
        res["answer"] = None
        res["explanation"] = "Cannot determine: " + res["honesty"]["basis"] + ". (The detector's own count was 0.)"
    res.pop("elements_used", None)
    res.pop("area_sources", None)
    w = res["honesty"].get("weakest")
    res["confidence"] = (None if res.get("answer") is None else 0.99 if res["honesty"]["deterministic"]
                         else round(w, 2) if w is not None else 0.5)
    if res["honesty"].get("style_warning") and res["confidence"] is not None:
        res["confidence"] = min(res["confidence"], 0.3)
    return res


TIERS = ["read", "measured", "detected", "estimated"]  # strongest first; a combined answer is as weak as its weakest part
UNIT_WORDS = {"m2": " m²", "m": " m", "m3": " m³", "count": "", "sqft": " sq ft", None: ""}


def combine(parts, how, read_by, sheet):
    """Several tool results -> one answer. parts = [(title, finished result)]. Every part is computed by the usual
    deterministic tools; this only adds, subtracts or compares them, and the answer is as weak as its weakest part."""
    plan = dict(intent="plan", combine=how, steps=[r["intent"] for _, r in parts])
    base = dict(intent=plan, question_read=read_by, source_type=sheet["source_type"], method="several measurements combined by code",
                evidence=[e for _, r in parts for e in r.get("evidence", [])], parts=[dict(title=t, answer=r.get("answer"), unit=r.get("unit"),
                tier=r["honesty"]["tier"], explanation=r.get("explanation")) for t, r in parts])
    fmt = lambda r: f"{r['answer']:g}{UNIT_WORDS.get(r['unit'], ' ' + str(r['unit']))}" if isinstance(r["answer"], (int, float)) else str(r["answer"])
    listing = "; ".join(f"{t}: {fmt(r) if r.get('answer') is not None else 'cannot determine'}" for t, r in parts)
    bad = [(t, r) for t, r in parts if r.get("answer") is None]
    numeric = all(isinstance(r.get("answer"), (int, float)) for _, r in parts)
    units = {r.get("unit") for _, r in parts}
    why = None
    if bad:
        why = "; ".join(f"{t}: {r.get('explanation')}" for t, r in bad)
    elif how != "each" and (not numeric or len(units) != 1):
        why = "the parts are not the same kind of quantity, so they cannot be combined"
    if why:
        return dict(base, answer=None, unit=None, confidence=None, explanation=f"{listing}. Not combined: {why}",
                    honesty=dict(tier="cannot determine", deterministic=None, basis=why))
    vals = [r["answer"] for _, r in parts]
    unit = next(iter(units)) if len(units) == 1 else None
    if how == "sum":
        ans, text = round(sum(vals), 2), f"{listing}. Sum = {round(sum(vals), 2):g}{UNIT_WORDS.get(unit, '')}."
    elif how == "difference":
        d = round(vals[0] - vals[1], 2)
        ans, text = d, f"{listing}. Difference (first minus second) = {d:g}{UNIT_WORDS.get(unit, '')}."
        if d < 0:  # "how much bigger is A than B" when A is the smaller one: say so in words, not only by the sign
            less = {"m2": "smaller", "sqft": "smaller", "count": "fewer"}.get(unit, "less")
            text += f" The answer is negative: {parts[0][0]} is {-d:g}{UNIT_WORDS.get(unit, '')} {less} than {parts[1][0]}, not more."
    elif how in ("largest", "smallest"):
        pick = (max if how == "largest" else min)(range(len(vals)), key=lambda i: vals[i])
        tie = sum(1 for v in vals if v == vals[pick]) > 1
        ans, unit = ("a tie" if tie else parts[pick][0]), None
        text = f"{listing}. " + ("They are equal." if tie else f"The {how} is {parts[pick][0]}.")
    else:  # each
        ans, unit, text = listing, None, listing + "."
    tiers = [r["honesty"]["tier"] for _, r in parts]
    tier = max(tiers, key=lambda t: TIERS.index(t) if t in TIERS else len(TIERS))
    confs = [r["confidence"] for _, r in parts if r.get("confidence") is not None]
    return dict(base, answer=ans, unit=unit, explanation=text, confidence=min(confs) if confs else None,
                honesty=dict(tier=tier, deterministic=all(r["honesty"].get("deterministic") for _, r in parts),
                             basis="; ".join(f"{t}: {r['honesty']['tier']}, {r['honesty']['basis']}" for t, r in parts)))


BUILDING_WIDE = re.compile(r"\b(model|building|whole|entire|all (floors|storeys|stories|levels)|every (floor|storey)|"
                           r"mudel\w*|hoone\w*|modellen|bygning\w*|Modell|Gebäude|gesamt\w*|rakennu\w*|koko)\b", re.I)
PLAN_STOREY_INTENTS = ("ifc_count", "count_openings", "count_fixtures", "opening_width")


def _on_plan_storey(sheet, intent, question):
    """A storey plan of a building model is open and the question names no storey: it is about the storey on screen
    ("how many doors are there?" on the 4th-floor plan), unless it says building / model / all floors."""
    st = sheet.get("plan_storey")
    if (st and sheet.get("source_type") == "ifc" and intent.get("intent") in PLAN_STOREY_INTENTS
            and not intent.get("storey") and not BUILDING_WIDE.search(question)):
        return dict(intent, storey=st, storey_from="plan")
    return intent


def _step(sheet, st, question, read_by):
    """One step of a model's plan, run and finished (with the plan's storey when it names none)."""
    st = _on_plan_storey(sheet, st, question)
    return _finish(sheet, st, _run(sheet, st), read_by)


def _model_plan(question, sheet):
    from . import llm_router
    return llm_router.parse(question, sheet)


def _model_read_by(plan, note, model):
    return dict(by="language model", model=model,
                note=f"read by a language model ({model}) as: {note} The model chose the tool"
                     + (" only" if len(plan["steps"]) == 1 else "s and how to combine them") + "; the values are computed by code.")


def solve(path, question, generic=False, llm=False):
    """llm=True: a question no pattern matches is read by a language model, which only chooses the tool calls and how
    to combine them (drawqa/llm_router.py), and a table question may get its query written by one (table_answer).
    The scoreboard leaves it off."""
    intent = router.parse(question)
    tabs = _tables(path)
    # a question the patterns read as a query on a building model is answered by the model, even when the plan PDF
    # has a room table with the same codes; only the table's title or the word "schedule" sends it to the table then
    about_table = bool(tabs) and names_a_table(question, tabs, values=not intent["intent"].startswith("ifc_"))
    if tabs and (about_table or intent["intent"] == "unknown"):  # before the sheet is converted: a table needs no detector run
        alt = table_answer(path, question, use_model=llm and about_table)
        if alt is not None:
            return alt
        if about_table:  # a table question the table could not answer: do not fall back to counting drawn symbols
            why = (f"This question is about a table on the sheet ({len(tabs)} found: " + ", ".join(repr(t["title"][:40]) for t in tabs[:4]) + "), "
                   "but it could not be turned into a query on it" + ("." if llm else " without a language model."))
            return dict(answer=None, unit=None, evidence=[], intent=dict(intent="schedule"), method="table query", source_type="vector-pdf",
                        confidence=None, explanation=why, question_read=dict(by="pattern", note="the question names a table on this sheet"),
                        honesty=dict(tier="cannot determine", deterministic=None, basis=why))
    sheet = load_sheet(path, generic)
    read_by = dict(by="pattern", note="matched a known question pattern")
    plan = None
    # on a building model, a question the patterns read as a plain drawing count ("doors on the second floor") may
    # name a storey those patterns ignore: let the model read it, and keep the pattern's reading if it cannot
    loose_on_ifc = sheet.get("source_type") == "ifc" and intent["intent"] not in ("unknown", "scale") and not intent["intent"].startswith("ifc_")
    asked_model = False
    if (intent["intent"] == "unknown" or loose_on_ifc) and llm and sheet.get("source_type") != "unknown":
        plan, note, model = _model_plan(question, sheet)
        asked_model = True
        if plan is not None:
            read_by = _model_read_by(plan, note, model)
            intent = plan["steps"][0]
        elif not loose_on_ifc:
            read_by = dict(by="nobody", note=note)
    if plan is not None and len(plan["steps"]) > 1:
        parts = [(st["title"], _step(sheet, st, question, read_by)) for st in plan["steps"]]
        return combine(parts, plan["combine"], read_by, sheet)
    if intent["intent"] == "unknown":
        res = dict(answer=None, explanation="This question was not understood, so nothing was measured: " + read_by["note"]
                   if read_by["by"] == "nobody" else
                   "This question does not match a known pattern, and free wording was not sent to a language model.")
    else:
        intent = _on_plan_storey(sheet, intent, question)
        res = _run(sheet, intent)
    if res.get("answer") is None and llm and not asked_model and sheet.get("source_type") != "unknown":
        # a known pattern matched, but its tool found nothing: the pattern may have misread free wording ("wall area
        # ... kitchen" read as the kitchen's floor area), so the model reads the question once; its plan is kept only
        # if it gives an answer
        plan2, note2, model2 = _model_plan(question, sheet)
        if plan2 is not None:
            rb2 = _model_read_by(plan2, note2, model2)
            if len(plan2["steps"]) > 1:
                out = combine([(st["title"], _step(sheet, st, question, rb2)) for st in plan2["steps"]], plan2["combine"], rb2, sheet)
                if out.get("answer") is not None:
                    return out
            else:
                res2 = _step(sheet, plan2["steps"][0], question, rb2)
                if res2.get("answer") is not None:
                    return res2
    if res.get("answer") is None:  # nothing from the drawing itself: maybe a table on the sheet answers it
        alt = table_answer(path, question, use_model=llm)
        if alt is not None:
            return alt
    return _finish(sheet, intent, res, read_by)
