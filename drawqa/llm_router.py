"""Free-form question -> the same intent the pattern router produces, chosen by a language model.

The model only picks WHICH deterministic tool to run and with which arguments. It never sees the drawing image and
never produces the number: counting, measuring and adding stay in query.py / ifc.py. (Published tests agree with our
own: language models read text well and count drawing symbols badly, so they are kept to the reading part.)

    parse(question, sheet) -> (intent dict or None, note)

Used only when the pattern router returns "unknown" and the caller asked for it (solve(..., llm=True)); the
scoreboard never calls it. Needs ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN); DRAWQA_LLM=0 switches it off.
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
# intent -> (arguments it needs, allowed values of cls or None)
INTENTS = {
    "count_openings": (("cls",), ["door", "window"]),
    "count_fixtures": (("cls",), FIXTURES),
    "count_rooms_class": (("cls",), ROOM_CLASSES),
    "count_rooms_label": (("label",), None),
    "count_units": ((), None),
    "count_floors": ((), None),
    "room_area": (("number",), None),
    "label_area": (("label",), None),
    "unit_area": (("unit",), None),
    "floor_area": ((), None),
    "scale": ((), None),
    "ifc_count": (("cls",), IFC_CLASSES),
    "ifc_room_area": (("name", "number", "storey"), None),
    "ifc_storey_area": (("storey",), None),
    "ifc_quantity": (("quantity", "cls", "storey"), ["wall", "slab"]),
    "unknown": ((), None),
}
SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": list(INTENTS)},
                   **{a: {"type": "string"} for a in ARGS},
                   "reading": {"type": "string"}},
    "required": ["intent", *ARGS, "reading"],
    "additionalProperties": False,
}

SYSTEM = f"""You route questions about one construction drawing to a fixed set of measuring tools.

How this is used: a person (often a judge testing the system) asks a question about a floor plan, a CAD file or a
building model, in any language and in their own words. Deterministic code then counts or measures on the drawing
and shows its evidence. Your only job is to say which tool answers the question and with which arguments. You do not
see the drawing and you never give the answer yourself.

The system is judged on honesty. A wrong tool produces a confident wrong number, which is the worst outcome, so
choose "unknown" whenever no tool answers what was actually asked (for example a question about materials, costs,
wall lengths on a plain floor plan, directions, or anything needing judgement), and say why in `reading`.

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
- label_area: total area of all rooms carrying an exact printed name. label = that name
- unit_area: indoor area of one apartment. unit = its letter, e.g. "B"
- floor_area: total indoor area of all rooms on the sheet
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
  means the room named "Küche"). If nothing listed matches, choose "unknown".
- When the drawing type is "ifc", use the ifc_* tools; otherwise never use them.
- Prefer a room-type count (count_rooms_class) when the drawing lists no room names, and a label-based tool when it does.
- Fill every argument the chosen tool does not need with an empty string.
- `reading`: one plain sentence saying how you read the question, or why no tool fits. It is shown to the user."""


def _context(sheet):
    """What the model may know about the drawing: its type and the names that exist on it. No geometry, no counts."""
    rooms = sheet.get("rooms", [])
    ctx = dict(type=sheet.get("source_type"), kind=sheet.get("kind"),
               room_names=sorted({r["name"] for r in rooms if r.get("name")})[:80],
               room_numbers=[r["number"] for r in rooms if r.get("number")][:120],
               apartments=sorted({u["label"] for u in sheet.get("units_list", []) if u.get("label")}),
               rooms_found_by_type=bool(sheet.get("room_classes_available")))
    if sheet.get("source_type") == "ifc" and sheet.get("model"):
        from . import ifc
        ctx["storeys"] = [s.Name for s in ifc._model(sheet["model"]).by_type("IfcBuildingStorey")]
    return json.dumps(ctx, ensure_ascii=False, sort_keys=True)


def _validate(d):
    """Model output -> intent dict in the router's contract, or None if it does not fit the tool it names."""
    it = d.get("intent")
    if it not in INTENTS or it == "unknown":
        return None
    need, classes = INTENTS[it]
    out = dict(intent=it)
    for a in need:
        v = (d.get(a) or "").strip()
        if not v and not (a == "storey" and it == "ifc_count"):
            return None
        out[a] = v or None
    if classes is not None and out.get("cls") not in classes:
        return None
    return out


@lru_cache(maxsize=256)
def _ask(question, context):
    import anthropic
    client = anthropic.Anthropic(timeout=40.0, max_retries=1)
    kw = dict(model=MODEL, max_tokens=4000, system=SYSTEM,
              output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
              messages=[{"role": "user", "content": f"Drawing: {context}\n\nQuestion: {question}"}])
    try:  # with a server-side fallback model, so a declined request is retried there instead of failing
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
    except anthropic.BadRequestError:  # e.g. fallbacks not available on this account: plain request
        resp = client.messages.create(**kw)
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"the language model stopped early ({resp.stop_reason})")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def parse(question, sheet):
    if os.environ.get("DRAWQA_LLM") == "0":
        return None, "the language model is switched off (DRAWQA_LLM=0)"
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None, "no language model is configured to read free wording (set ANTHROPIC_API_KEY)"
    try:
        import anthropic
    except ImportError:
        return None, "the anthropic package is not installed"
    try:
        d = _ask(question.strip(), _context(sheet))
    except anthropic.AuthenticationError:
        return None, "the language model rejected the API key"
    except anthropic.RateLimitError:
        return None, "the language model is rate limited right now"
    except anthropic.APIStatusError as e:
        return None, f"the language model returned an error ({e.status_code})"
    except anthropic.APIConnectionError:
        return None, "the language model could not be reached (network)"
    except (RuntimeError, ValueError, StopIteration) as e:
        return None, str(e) or "the language model gave no usable reply"
    intent = _validate(d)
    reading = (d.get("reading") or "").strip()
    if intent is None and d.get("intent") != "unknown":  # it named a tool but with arguments that tool does not take
        return None, f"the language model's choice ({d.get('intent')}) did not fit any tool's arguments, so it was not run"
    if intent is None:
        return None, reading or "the language model found no tool that answers this question"
    return intent, reading
