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
ARGS = ("cls", "label", "number", "unit", "storey", "quantity", "name")
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
    "ifc_count": (("cls",), IFC_CLASSES),
    "ifc_room_area": (("name", "number", "storey"), None),
    "ifc_storey_area": (("storey",), None),
    "ifc_quantity": (("quantity", "cls", "storey"), ["wall", "slab"]),
    "unknown": ((), None),
}
COMBINE = ["single", "sum", "difference", "largest", "smallest", "each"]
MAX_STEPS = 6
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
    "properties": {"steps": {"type": "array", "items": STEP}, "combine": {"type": "string", "enum": COMBINE}, "reading": {"type": "string"}},
    "required": ["steps", "combine", "reading"],
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
Steps that are combined must measure the same kind of thing (all areas, or all counts).

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
  "the kitchen" = ["Cucina"]). Use as few names as the question needs
- unit_area: indoor area of one apartment. unit = its letter, e.g. "B"
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

Rules:
- The user message describes the drawing: its type and the room names, room numbers, apartment letters or storey
  names that exist on it. When a tool needs a label, a number or a storey, use one of those values exactly as
  listed, matching the question's wording to it across languages (a question about "the kitchen" on a German plan
  means the room named "Küche"). If nothing listed matches, return no steps.
- When the drawing type is "ifc", use the ifc_* tools; otherwise never use them.
- Prefer a room-type count (count_rooms_class) when the drawing lists no room names, and a label-based tool when it does.
- A question about how much material to order for certain rooms (tiles, flooring, paint) is answered by the floor
  area of those rooms: choose the area tool, and say in `reading` that it gives floor area only (wall tiling would
  need wall heights, which a floor plan does not show).
- Fill every text argument the chosen tool does not need with an empty string, and every list it does not need
  with an empty list.
- `reading`: one or two plain sentences, shown to the user: how you read the question, or why no tool fits. When the
  tool measures less than what was asked, say exactly what is left out (for a tiling order: "this is the floor area
  of the rooms; wall tiles, waste allowance and anything not drawn on the plan are not included")."""


def _context(sheet):
    """What the model may know about the drawing: its type and the names that exist on it. No geometry, no counts."""
    rooms = sheet.get("rooms", [])
    ctx = dict(type=sheet.get("source_type"), kind=sheet.get("kind"),
               room_names=sorted({r["name"] for r in rooms if r.get("name")})[:80],
               room_numbers=[r["number"] for r in rooms if r.get("number")][:120],
               apartments=sorted({u["label"] for u in sheet.get("units_list", []) if u.get("label")}),
               rooms_found_by_type=bool(sheet.get("room_classes_available")),
               room_types_found=sorted({r["cls"] for r in rooms if r.get("cls") and r["cls"] != "unlabelled"}))
    if sheet.get("source_type") == "ifc" and sheet.get("model"):
        from . import ifc
        ctx["storeys"] = [s.Name for s in ifc._model(sheet["model"]).by_type("IfcBuildingStorey")]
    return json.dumps(ctx, ensure_ascii=False, sort_keys=True)


def _validate_step(d):
    """One step of the model's plan -> intent dict in the router's contract, or None if it does not fit the tool it names."""
    it = d.get("intent")
    if it not in INTENTS or it == "unknown":
        return None
    need, classes = INTENTS[it]
    out = dict(intent=it)
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
    return out


def _validate(d):
    """Model output -> dict(steps=[intent dicts, each with a title], combine) or None when it is not a runnable plan."""
    steps = d.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
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
    how = d.get("combine") if d.get("combine") in COMBINE else None
    if len(out) == 1:
        how = "single"
    elif how in (None, "single") or (how == "difference" and len(out) != 2):
        return None
    return dict(steps=out, combine=how)


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
             + '}], "combine": "<' + " | ".join(COMBINE) + '>", "reading": "..."}. Every step carries all of those keys; '
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
    plan = _validate(d)
    reading = str(d.get("reading") or "").strip()
    if plan is None and d.get("steps"):  # it named tools, but with arguments they do not take or an unusable combination
        named = ", ".join(str(st.get("intent")) for st in d["steps"] if isinstance(st, dict))[:120]
        return None, f"the language model's plan ({named}; combine {d.get('combine')}) did not fit the tools, so it was not run", model
    if plan is None:
        return None, reading or "the language model found no tool that answers this question", model
    return plan, reading, model
