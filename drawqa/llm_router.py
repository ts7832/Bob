"""Free-form question -> the same intent the pattern router produces, chosen by a language model.

The model only picks WHICH deterministic tool to run and with which arguments. It never sees the drawing image and
never produces the number: counting, measuring and adding stay in query.py / ifc.py. (Published tests agree with our
own: language models read text well and count drawing symbols badly, so they are kept to the reading part.)

    parse(question, sheet) -> (plan = dict(steps=[tool calls], combine) or None, note, model name)

Used only when the pattern router returns "unknown" and the caller asked for it (solve(..., llm=True)); the
scoreboard never calls it. DRAWQA_LLM=0 switches it off. Two backends, same prompt, same checks on the reply:
  1. Claude API with a JSON schema, when ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) is set;
  2. otherwise the open model already deployed on Modal (drawqa.reader.text, Qwen3-VL-32B, no key): no schema
     guarantee, so the JSON is cut out of its text; 1-3 s when the service is warm, minutes when it is cold.
"""
import json, os
from functools import lru_cache

MODEL = os.environ.get("DRAWQA_LLM_MODEL", "claude-opus-5-5")
FIXTURES = ["wc", "sink", "bathtub", "shower", "shower+bathtub"]
ROOM_CLASSES = ["bedroom", "kitchen", "bathroom", "wc_room", "balcony", "sauna", "living_room", "dining", "entry",
                "storage", "utility", "dressing_room", "terrace", "garage"]
IFC_CLASSES = ["door", "window", "wall", "column", "beam", "stair", "space", "slab", "sanitary"]
QUANTITIES = ["net volume", "net side area", "length"]
ARGS = ("cls", "label", "number", "unit", "storey", "quantity", "name", "height")
LISTS = ("labels", "classes")  # several printed names / several room types in one question ("the toilets")
# intent -> (arguments it needs, allowed values of cls or None)
INTENTS = {
    "count_openings": (("cls",), ["door", "window"]),
    "count_fixtures": (("cls",), FIXTURES),
    "count_rooms_class": (("cls",), ROOM_CLASSES),
    "count_rooms_label": (("label",), None),
    "count_units": ((), None),
    "count_floors": ((), None),
    "room_area": (("number",), None),
    "label_area": (("labels",), None),
    "unit_area": (("unit",), None),
    "floor_area": ((), None),
    "class_area": (("classes",), None),
    "wall_length": ((), None),
    "scale": ((), None),
    "ifc_count": (("cls", "storey"), IFC_CLASSES),  # storey may be empty: the whole model
    "ifc_room_area": (("name", "number", "storey"), None),
    "ifc_storey_area": (("storey",), None),
    "ifc_quantity": (("quantity", "cls", "storey"), ["wall", "slab"]),
    "opening_width": (("cls",), ["door", "window"]),
    "room_perimeter": ((), None),
    "wall_area": ((), None),
    # read from the drawing's own text (PDF text layer, CAD text, or OCR of an image): drawqa/tags.py, drawqa/slabs.py
    "count_rooms": ((), None),
    "count_tags": (("label",), None),
    "find_text": (("label",), None),
    "read_value": (("label",), None),
    "pipe_size": (("label",), None),
    "slab_areas": ((), None),
    "unknown": ((), None),
}
ROOM_PICK = ("labels", "classes", "number", "unit")  # which rooms; none of them = all indoor rooms
OPTIONAL = {"opening_width": ("quantity", "storey"), "room_perimeter": ROOM_PICK, "wall_area": ROOM_PICK + ("height",),
            "label_area": ("unit",)}
COMBINE = ["single", "sum", "difference", "largest", "smallest", "each"]
MAX_STEPS = 8
EVERY_STOREY = ("ifc_count", "ifc_storey_area", "ifc_quantity")  # tools a storey = "*" step may be repeated with
MAX_STOREYS = 60
STEP = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "intent": {"type": "string", "enum": list(INTENTS)},
                   **{a: {"type": "string"} for a in ARGS},
                   **{a: {"type": "array", "items": {"type": "string"}} for a in LISTS}},
    "required": ["title", "intent", *ARGS, *LISTS],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "array", "items": STEP}, "combine": {"type": "string", "enum": COMBINE}, "reading": {"type": "string"},
                   "adjust": {"type": "object", "properties": {"op": {"type": "string", "enum": ["none", "plus", "minus", "times", "divide"]},
                                                               "value": {"type": "number"}},
                              "required": ["op", "value"], "additionalProperties": False}},
    "required": ["steps", "combine", "reading", "adjust"],
    "additionalProperties": False,
}

SYSTEM = f"""You route questions about one construction drawing to a fixed set of measuring tools.

How this is used: a person (often a judge testing the system) asks a question about a floor plan, a CAD file or a
building model, in any language and in their own words. Deterministic code then counts or measures on the drawing
and shows its evidence. Your only job is to say which tool calls answer the question, with which arguments, and how
their results are combined. You do not see the drawing and you never give the answer yourself.

The system is judged on honesty. A wrong tool produces a confident wrong number, which is the worst outcome, so
return an empty list of steps whenever no tool answers what was actually asked (for example a question about
materials, costs, directions, or anything needing judgement), and say why in `reading`.

Your reply is a plan: `steps` (tool calls, each with a short `title` naming what it measures, e.g. "Apartment A"),
`combine` and `reading`. Almost every question needs ONE step with combine = "single". Use more steps only when the
question compares or adds things one tool call cannot give, at most {MAX_STEPS}:
- "which apartment is bigger, A or B?" = two unit_area steps, combine = "largest" ("smallest" for the smaller one)
- "how much bigger is A than B?" = the same two steps in that order, combine = "difference" (first minus second)
- "how many doors and windows are there together?" = two count steps, combine = "sum"
- "what are the areas of apartments A, B and C?" = three steps, combine = "each"
- a question about every storey of a building model ("how many rooms are on each storey?", "which storey has the
  largest floor area?") = ONE step with storey = "*" and combine = "each" (or "largest" / "smallest"); code repeats
  that step for every storey, so do not list the storeys yourself
Steps that are combined must measure the same kind of thing (all areas, or all counts).
`adjust` is arithmetic with a number the question itself gives, done by code on the final value: "total area minus
10" = op "minus", value 10; "add 10% for waste" = op "times", value 1.1; "half of it" = op "divide", value 2.
Otherwise op "none", value 0. Never put a measured value there. Units asked for (square feet, cm) are converted by
code: do not use adjust for them.

Tools (intent: what it does; arguments):
- count_openings: number of doors or windows drawn. cls = door | window
- count_fixtures: number of sanitary fixtures drawn. cls = wc | sink | bathtub | shower | shower+bathtub
  (sink covers wash basins and kitchen sinks; shower+bathtub is their sum)
- count_rooms_class: number of rooms of a type. cls = {" | ".join(ROOM_CLASSES)}
  (bathroom = room with a shower or bath; wc_room = separate toilet room)
- count_rooms_label: number of rooms carrying an exact printed name. label = that name, copied from the room names
  listed for the drawing
- count_units: number of apartments on the sheet
- count_floors: number of storeys shown on the sheet
- room_area: area of one room identified by its printed room number. number = e.g. "2.03"
- label_area: total area of all rooms whose printed name is one of `labels`. labels = every listed room name that
  the question covers, copied exactly ("the toilets" on an Italian plan listing Bagno and WC = ["Bagno", "WC"];
  "the kitchen" = ["Cucina"]). Use as few names as the question needs. When the question names one apartment / unit
  and the same room name occurs in several, also set unit = that apartment's tag from `apartments` ("the living
  room in unit U212" = labels ["1BD - LIVING"], unit "U212"); otherwise unit = ""
- unit_area: indoor area of one apartment / unit. unit = its letter or tag as listed in `apartments`, e.g. "B" or
  "U208" (a total printed on the sheet is used when there is one)
- count_rooms: number of rooms the drawing names (each room label, with or without a printed area)
- floor_area: total indoor area of all rooms on the sheet
- class_area: total area of all rooms of the given types, for drawings whose rooms are found by type
  (rooms_found_by_type is true). classes = the room types the question covers, from the list above and preferably
  from room_types_found ("the toilets" = ["wc_room", "bathroom"] when both exist)
- wall_length: total length of the walls drawn on an image (type "raster"); not for building models
- scale: the drawing's scale and where it came from
- ifc_count (building models only): number of elements. cls = {" | ".join(IFC_CLASSES)}; storey = a storey name
  from the list, or empty for the whole model
- ifc_room_area (building models only): net floor area of one room. name, number, storey
- ifc_storey_area (building models only): total net floor area of all rooms on a storey. storey
- ifc_quantity (building models only): quantity = {" | ".join(QUANTITIES)}; cls = wall | slab; storey
- opening_width: widths of the doors or windows, in metres (all drawing types, building models too). cls = door |
  window; quantity = "largest" for the widest one, "smallest" for the narrowest, "" for all of them grouped by width;
  storey = a storey name on building models, else ""
- room_perimeter (not for building models): total length around the chosen rooms (e.g. skirting boards). Choose the
  rooms like the area tools: labels (printed names), or classes (room types, when rooms_found_by_type), or number,
  or unit; leave all four empty for all indoor rooms
- count_tags: number of items identified by a tag printed on the drawing (valves, pumps, pipe supports, equipment:
  CV-1 ... CV-5 = 5). label = ONE prefix from `tag_prefixes`; choose it by the meaning the sheet gives it (listed with
  the prefix) or by the question's own words ("check valves" = CV when CV means check valve). Several kinds asked for
  together = one step per prefix, combine "sum" (or "each" for one count per kind)
- find_text: the drawing's own text lines containing some words: a note, a callout, a specification, a title.
  label = the words to look for, e.g. "NOTE 4" or "SLAB-ON-GRADE". The answer is the text as printed
- read_value: a number printed next to a label in a list or table on the sheet (area lists, room counts:
  "WEST WING DORMITORY = 6,095 SF"). label = the label as listed in `labelled_values`, without the unit in brackets
  (`labelled_values` shows each label with the unit of its value: "FIRST FLOOR (DORMS)" answers "how many dorms
  are on the first floor?" with label "FIRST FLOOR")
- pipe_size: the size printed with a named pipe ("16\" SUCTION HEADER"). label = the pipe's name, e.g.
  "suction header"
- slab_areas: floor-slab areas grouped by slab type (thickness and reinforcement), from the slab callouts and the
  dimensions; only when `slab_callouts` is above 0
- wall_area (not for building models): wall surface of the chosen rooms = their perimeter times the room height
  (e.g. wall tiles, paint). Rooms chosen as for room_perimeter. height = a room or ceiling height the question
  gives, as a number in metres ("2.7"), else "" (code then assumes a height and says so)

Rules:
- The user message describes the drawing: its type and the room names, room numbers, apartment letters or storey
  names that exist on it. When a tool needs a label, a number or a storey, use one of those values exactly as
  listed, matching the question's wording to it across languages (a question about "the kitchen" on a German plan
  means the room named "Küche"). If nothing listed matches, return no steps.
- When the drawing type is "ifc", use the ifc_* tools (and opening_width); otherwise never use the ifc_* tools.
- When `this_plan_shows_storey` is given, the person is looking at the plan of that one storey: a question that names
  no storey ("how many doors are there?") is about that storey, so set storey to it. Use the whole model (storey "")
  only when the question says building, model or all floors.
- Prefer a room-type count (count_rooms_class) when the drawing lists no room names, and a label-based tool when it does.
  But a label stands for a room type only when the name clearly means that type (Schlafzimmer, Camera da letto,
  MAKUUH = bedroom; Bagno = bathroom). A generic word for "room" (Stanza, Zimmer, Pièce, Huone, Room, Rum) does not
  say which kind of room it is: for "how many bedrooms" on a plan whose rooms are only "Stanza", return no steps and
  say in `reading` that the plan names its rooms only "Stanza" and does not say which are bedrooms.
- A question about how much material to order for certain rooms: flooring or floor tiles = the floor area of those
  rooms (an area tool); paint or wall tiles = wall_area; skirting = room_perimeter. "Tiling" with no word about walls
  means floor tiling: choose the area tool, and say in `reading` that it is the floor area only and that the wall
  area can be asked for separately.
- Fill every text argument the chosen tool does not need with an empty string, and every list it does not need
  with an empty list.
- `reading`: one or two plain sentences, shown to the user: how you read the question, or why no tool fits. When the
  tool measures less than what was asked, say exactly what is left out (for a tiling order: "this is the floor area
  of the rooms; wall tiles, waste allowance and anything not drawn on the plan are not included")."""


def _text_context(sheet):
    """What the drawing's own text offers the text tools: tag prefixes with the meaning the sheet gives them, labels of
    printed values, slab callouts. Names only, no counts or values (the model chooses tools, code reads the numbers).
    Empty when the modules are not there yet or the sheet has no text."""
    texts = sheet.get("texts") or []
    if not texts:
        return {}
    out = {}
    try:
        from . import tags
        inv = tags.inventory(texts)
        pre = sorted(inv.items(), key=lambda kv: -len(kv[1].get("numbers") or kv[1].get("tags") or []))[:25]
        if pre:
            meaning = lambda d: (d.get("text") if isinstance(d, dict) else d) or ""
            out["tag_prefixes"] = {p: meaning(v.get("definition")) for p, v in pre}
        kv = tags.key_values(texts)
        if kv:  # each label with the unit its value is printed in ("FIRST FLOOR (DORMS)"), never the value itself
            out["labelled_values"] = sorted({str(x.get("key") or x.get("label")) + (f" ({x['unit']})" if x.get("unit") else "")
                                             for x in kv if x.get("key") or x.get("label")})[:40]
    except Exception:  # module not ready, or a sheet it cannot read: the other tools still work
        pass
    try:
        from . import slabs
        out["slab_callouts"] = len(slabs.callouts(texts))
    except Exception:
        pass
    return out


def _context(sheet):
    """What the model may know about the drawing: its type and the names that exist on it. No geometry, no counts."""
    rooms = sheet.get("rooms", [])
    ctx = dict(type=sheet.get("source_type"), kind=sheet.get("kind"),
               room_names=sorted({r["name"] for r in rooms if r.get("name")} | {n["label"] for n in sheet.get("printed_names", [])})[:80],
               room_numbers=[r["number"] for r in rooms if r.get("number")][:120],
               apartments=sorted({u["label"] for u in sheet.get("units_list", []) if u.get("label")}),
               rooms_found_by_type=bool(sheet.get("room_classes_available")),
               rooms_have_printed_areas=any(r.get("area_source") == "printed label" for r in rooms),
               room_types_found=sorted({r["cls"] for r in rooms if r.get("cls") and r["cls"] != "unlabelled"}))
    ctx.update(_text_context(sheet))
    if sheet.get("source_type") == "ifc" and sheet.get("model"):
        from . import ifc
        ctx["storeys"] = [s.Name for s in ifc._model(sheet["model"]).by_type("IfcBuildingStorey")]
        if sheet.get("plan_storey"):
            ctx["this_plan_shows_storey"] = sheet["plan_storey"]
    return json.dumps(ctx, ensure_ascii=False, sort_keys=True)


def _validate_step(d):
    """One step of the model's plan -> intent dict in the router's contract, or None if it does not fit the tool it names."""
    it = d.get("intent")
    if it not in INTENTS or it == "unknown":
        return None
    d = dict(d)
    if it == "room_area" and not str(d.get("number") or "").strip() and (str(d.get("label") or "").strip() or d.get("labels")):
        it = d["intent"] = "label_area"  # "area of the OLOH": a printed name given to the room-number tool
    need, classes = INTENTS[it]
    out = dict(intent=it)
    # the open model sometimes puts a single value in the list field next to it, or the other way round
    for one, many in (("cls", "classes"), ("label", "labels")):
        vals = [str(x).strip() for x in (d.get(many) or []) if str(x).strip()]
        if one in need and not str(d.get(one) or "").strip() and len(vals) == 1:
            d[one] = vals[0]
        if many in need and not vals and str(d.get(one) or "").strip():
            d[many] = [str(d[one]).strip()]
    for a in need:
        if a in LISTS:
            v = [str(x).strip() for x in (d.get(a) or []) if str(x).strip()]
            if not v or (a == "classes" and any(x not in ROOM_CLASSES + ["sauna_room"] for x in v)):
                return None
            out[a] = v
            continue
        v = str(d.get(a) or "").strip()
        if not v and not (a == "storey" and it == "ifc_count"):
            return None
        out[a] = v or None
    if classes is not None and out.get("cls") not in classes:
        return None
    for a in OPTIONAL.get(it, ()):  # arguments a tool takes when given; checked like the required ones
        if a in LISTS:
            v = [str(x).strip() for x in (d.get(a) or []) if str(x).strip()]
            if a == "classes" and any(x not in ROOM_CLASSES + ["sauna_room"] for x in v):
                return None
        else:
            v = str(d.get(a) or "").strip()
            if a == "quantity" and v.lower() not in ("largest", "smallest"):
                v = ""
        if v:
            out[a] = v
    return out


def _validate(d):
    """Model output -> dict(steps=[intent dicts, each with a title], combine) or None when it is not a runnable plan."""
    steps = d.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    # the open model sometimes puts several values in one step's list for a tool that takes one ("count_rooms_class"
    # with classes = [bedroom, bathroom]): split it into one step per value, added up when it was the only step
    split = []
    for st in steps:
        if isinstance(st, dict) and st.get("intent") in INTENTS:
            need = INTENTS[st["intent"]][0]
            for one, many in (("cls", "classes"), ("label", "labels")):
                vals = [str(x).strip() for x in (st.get(many) or []) if str(x).strip()]
                if one in need and many not in need and not str(st.get(one) or "").strip() and len(vals) > 1:
                    split.extend(dict(st, **{one: v, many: [], "title": v}) for v in vals)
                    break
            else:
                split.append(st)
        else:
            split.append(st)
    if len(split) > len(steps):
        if len(steps) == 1 and d.get("combine") in (None, "single", ""):
            d = dict(d, combine="sum")
        steps = split
    if len(steps) > MAX_STEPS:
        return None
    out = []
    for st in steps:
        if not isinstance(st, dict):
            return None
        v = _validate_step(st)
        if v is None:
            return None
        v["title"] = str(st.get("title") or "").strip() or v["intent"]
        out.append(v)
    titles = [v["title"] for v in out]
    for v in out:  # two steps with the same title would print as "X: 1; X: 2": name them by what they measure
        if titles.count(v["title"]) > 1:
            arg = next((v[a] for a in ("cls", "label", "unit", "number", "name", "storey", "classes", "labels") if v.get(a)), None)
            if arg:
                v["title"] = ", ".join(arg) if isinstance(arg, list) else str(arg)
    how = d.get("combine") if d.get("combine") in COMBINE else None
    every = len(out) == 1 and out[0].get("storey") == "*"  # one step repeated for every storey (expanded in parse)
    if every:
        if out[0]["intent"] not in EVERY_STOREY or how not in ("each", "largest", "smallest", "sum"):
            return None
    elif len(out) == 1:
        how = "single"
    elif how in (None, "single") or (how == "difference" and len(out) != 2):
        return None
    plan = dict(steps=out, combine=how)
    adj = d.get("adjust") if isinstance(d.get("adjust"), dict) else {}
    try:
        val = float(adj.get("value"))
    except (TypeError, ValueError):
        val = None
    if adj.get("op") in ("plus", "minus", "times", "divide") and val is not None and not (adj["op"] == "divide" and val == 0):
        plan["adjust"] = dict(op=adj["op"], value=val)
    return plan


def _has_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


@lru_cache(maxsize=256)
def _ask(question, context):
    """-> (reply dict, model name). Raises RuntimeError with a plain reason when no usable reply came back."""
    user = f"Drawing: {context}\n\nQuestion: {question}"
    if not _has_key():
        return _ask_open(user)
    import anthropic
    client = anthropic.Anthropic(timeout=40.0, max_retries=1)
    kw = dict(model=MODEL, max_tokens=4000, system=SYSTEM,
              output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
              messages=[{"role": "user", "content": user}])
    try:  # with a server-side fallback model, so a declined request is retried there instead of failing
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
    except anthropic.BadRequestError:  # e.g. fallbacks not available on this account: plain request
        resp = client.messages.create(**kw)
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"the language model stopped early ({resp.stop_reason})")
    return json.loads(next(b.text for b in resp.content if b.type == "text")), resp.model


JSON_ONLY = ("\n\nReply with one JSON object and nothing else, of this exact form: "
             '{"steps": [{"title": "...", "intent": "<one of: ' + ", ".join(k for k in INTENTS if k != "unknown") + '>", '
             + ", ".join(f'"{a}": ""' for a in ARGS) + ", " + ", ".join(f'"{a}": []' for a in LISTS)
             + '}], "combine": "<' + " | ".join(COMBINE) + '>", "reading": "...", "adjust": {"op": "none", "value": 0}}. '
             'Every step carries all of those keys; '
             'text arguments a tool does not need are "", lists it does not need are []. No tool fits: "steps": [].')


def _ask_open(user):
    """The open model on Modal. It has no schema guarantee: the first {...} in its text is parsed, and _validate
    refuses anything that does not fit a tool."""
    try:
        from . import reader
    except ImportError:
        raise RuntimeError("no language model is configured (no ANTHROPIC_API_KEY, and drawqa/reader.py is missing)")
    r = reader.text(user, 700, SYSTEM + JSON_ONLY)
    if r.get("error"):
        raise RuntimeError(r["error"])
    t = r.get("text") or ""
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise RuntimeError("the language model's reply was not JSON")
    try:
        return json.loads(t[i:j + 1]), str(r.get("model", "open model")).split("/")[-1]
    except ValueError:
        raise RuntimeError("the language model's reply was not valid JSON")


GENERIC_ROOM_WORDS = {"stanza", "stanze", "zimmer", "raum", "piece", "pièce", "huone", "room", "rum", "rom", "ruum", "camera"}
TYPE_WORDS = r"bed ?rooms?|kitchens?|bath ?rooms?|living ?rooms?|makuuh\w*|schlafzimmer|chambres?|camere da letto|soveværelse\w*|magamistuba\w*"


def _generic_room_label(d, question):
    """The question asks for a room type ("how many bedrooms") and the plan answers it with a generic word for "room"
    (Stanza): refuse with the reason, instead of counting every room as a bedroom. A question that itself names the
    word ("how many rooms are labelled Stanza") is not affected. ("camera" alone is generic in Italian; "camera da
    letto" is a bedroom and is not one label word.)"""
    import re
    if not re.search(TYPE_WORDS, question, re.I):
        return None
    for st in d.get("steps") or []:
        if not isinstance(st, dict) or st.get("intent") not in ("count_rooms_label", "label_area"):
            continue
        labels = [str(x) for x in (st.get("labels") or [])] + ([str(st["label"])] if st.get("label") else [])
        generic = [l for l in labels if l.strip().lower() in GENERIC_ROOM_WORDS]
        if generic and not any(re.search(rf"(?<!\w){re.escape(g)}(?!\w)", question, re.I) for g in generic):
            return (f"the plan names these rooms only '{generic[0]}', a general word for 'room', so it does not say which of "
                    f"them are of the kind asked for; ask about '{generic[0]}' itself to count or measure those rooms")
    return None


from .router import NOT_A_ROOM  # words that do not name a room (one list, in router.py)


AREA_UNITS = {"SF", "SQFT", "SQ FT", "M2", "M²", "SQM"}


def _fit_value_kind(d, question, sheet):
    """read_value: a question about an AREA must get a label whose value is an area, a "how many" question one whose
    value is not. When the model's label has the wrong kind and a label that contains its words has the right kind
    ("total area of the second floor": SECOND FLOOR = 48 DORMS -> TOTAL SECOND FLOOR = 26,562 SF), take that one."""
    import re
    steps = [st for st in d.get("steps") or [] if isinstance(st, dict) and st.get("intent") == "read_value" and st.get("label")]
    if not steps:
        return
    wants_area = bool(re.search(r"\barea|square|\bsq\b|m²|\bm2\b|footage|\bsize\b", question, re.I))
    wants_count = bool(re.search(r"\bhow many\b|\bnumber of\b", question, re.I))
    if wants_area == wants_count:
        return
    try:
        from . import tags
        kv = tags.key_values(sheet.get("texts") or [])
    except Exception:
        return
    words = lambda s: set(re.findall(r"[A-Z0-9]+", str(s).upper()))
    is_area = lambda x: str(x.get("unit") or "").upper().strip() in AREA_UNITS
    for st in steps:
        lab = re.sub(r"\s*\([^)]*\)\s*$", "", st["label"])
        cur = [x for x in kv if words(x.get("label")) == words(lab)]
        if cur and is_area(cur[0]) == wants_area:
            continue
        alt = [x for x in kv if words(lab) <= words(x.get("label")) and is_area(x) == wants_area]
        if alt:
            st["label"] = min(alt, key=lambda x: len(words(x.get("label"))))["label"]


def _named_room_missing(d, question):
    """A measuring step over ALL rooms (room_perimeter / wall_area / room areas with no room chosen) while the question
    names one room ("skirting for the library"): the model found no such room and fell back to everything. Refuse with
    the reason instead of measuring the whole plan."""
    import re
    from .router import names_specific_room
    whole = re.search(r"\b(all|every|each|whole|entire|total)\b", question, re.I)
    for st in d.get("steps") or []:
        if not isinstance(st, dict) or st.get("intent") not in ("room_perimeter", "wall_area", "floor_area"):
            continue
        if any(str(st.get(k) or "").strip() for k in ("number", "unit", "label")) or st.get("labels") or st.get("classes"):
            continue
        named = names_specific_room(question)
        if not named and not whole:
            m = [w for w in re.findall(r"\bthe\s+([a-z][\w\-]{3,30})\b", question, re.I) if w.lower() not in NOT_A_ROOM]
            named = m[0] if m else None
        if named:
            return (f"the question is about '{named}', which is not among the rooms named on this drawing, so nothing was "
                    "measured (measuring every room instead would answer a different question)")
    return None


def _fill_room_names(d, question, sheet):
    """A room tool sent back with no room ("area of the OLOH" -> room_area with every argument empty and the name only
    in its title): take the room names that exist on this drawing and appear, as whole words, in the question or the
    step's title. Only names listed for the drawing are used, so nothing is invented."""
    import re
    ctx = json.loads(_context(sheet))
    names, numbers = ctx.get("room_names") or [], set(ctx.get("room_numbers") or [])
    by_low = {n.lower(): n for n in names}
    # the question's room words all appear in printed names ("the secondary classrooms" -> "Secondary Classroom",
    # "Secondary Classroom (Severe Needs)"): those names, ahead of a room TYPE the model may have picked (it once took
    # classrooms for bedrooms, because the detector types school rooms as apartment rooms)
    from .router import room_phrase, names_matching
    match = names_matching(room_phrase(question), names)
    if match:
        swap = {"class_area": "label_area", "count_rooms_class": "count_rooms_label", "label_area": "label_area",
                "count_rooms_label": "count_rooms_label", "room_perimeter": "room_perimeter", "wall_area": "wall_area"}
        for st in d.get("steps") or []:
            if isinstance(st, dict) and st.get("intent") in swap and not st.get("number") and not st.get("unit"):
                # a count takes one name per step: several matching names are split into steps and added by _validate
                one = match[0] if len(match) == 1 or swap[st["intent"]] != "count_rooms_label" else ""
                st.update(intent=swap[st["intent"]], labels=match, label=one, cls="", classes=[])
                if not one and d.get("combine") in (None, "", "single"):
                    d["combine"] = "sum"
    for st in d.get("steps") or []:
        if not isinstance(st, dict) or st.get("intent") not in ("room_area", "label_area", "count_rooms_label"):
            continue
        num = str(st.get("number") or "").strip()
        if st["intent"] == "room_area" and num and num not in numbers and num.lower() in by_low:
            st.update(intent="label_area", number="", labels=[by_low[num.lower()]], label="")  # a name in the number field
            continue
        if any(str(st.get(k) or "").strip() for k in ("number", "label")) or st.get("labels"):
            continue
        text = f"{st.get('title') or ''} {question}"
        found = [n for n in names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.I)]
        if found:
            st["labels"], st["label"] = found, found[0]


def parse(question, sheet):
    """-> (plan dict(steps, combine) or None, note, model name or None)."""
    if os.environ.get("DRAWQA_LLM") == "0":
        return None, "the language model is switched off (DRAWQA_LLM=0)", None
    try:
        if _has_key():
            import anthropic
            try:
                d, model = _ask(question.strip(), _context(sheet))
            except anthropic.AuthenticationError:
                return None, "the language model rejected the API key", None
            except anthropic.RateLimitError:
                return None, "the language model is rate limited right now", None
            except anthropic.APIStatusError as e:
                return None, f"the language model returned an error ({e.status_code})", None
            except anthropic.APIConnectionError:
                return None, "the language model could not be reached (network)", None
        else:
            d, model = _ask(question.strip(), _context(sheet))
    except (RuntimeError, ValueError, StopIteration, ImportError) as e:
        return None, str(e) or "the language model gave no usable reply", None
    if not isinstance(d, dict):
        return None, "the language model's reply was not a JSON object", model
    _fill_room_names(d, question, sheet)
    generic = _generic_room_label(d, question)
    if generic:
        return None, generic, model
    missing = _named_room_missing(d, question)
    if missing:
        return None, missing, model
    _fit_value_kind(d, question, sheet)
    plan = _validate(d)
    reading = str(d.get("reading") or "").strip()
    if plan is None and d.get("steps"):  # it named tools, but with arguments they do not take or an unusable combination
        named = ", ".join(str(st.get("intent")) for st in d["steps"] if isinstance(st, dict))[:120]
        return None, f"the language model's plan ({named}; combine {d.get('combine')}) did not fit the tools, so it was not run", model
    if plan is None:
        return None, reading or "the language model found no tool that answers this question", model
    if plan["steps"][0].get("storey") == "*":  # repeat the one step for every storey the model has, in its order
        storeys = [s for s in json.loads(_context(sheet)).get("storeys") or [] if s]
        if not storeys or len(storeys) > MAX_STOREYS:
            return None, ("the language model asked for every storey, but this drawing has no storeys" if not storeys
                          else f"the language model asked for every storey, and this model has {len(storeys)}, too many to list"), model
        st = plan["steps"][0]
        plan = dict(plan, steps=[dict(st, storey=s, title=s) for s in storeys])
        reading = (reading + " " if reading else "") + f"Repeated for each of the {len(storeys)} storeys by code."
    return plan, reading, model
