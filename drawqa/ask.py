"""Answer any question about any drawing, picture first.

    ask(path, question, page=0) -> dict(answer, unit, tier, basis, evidence=[{id, cls, bbox}], route, seconds, notes)

1. Engine first (drawqa.solve on the page the layer was read from): when its pattern router knows the question, the
   answer is computed by code, deterministic, with its own honesty tier.
2. Otherwise the answer agent: a language model (drawqa/llm.py: Kimi on Modal now, Claude later) that sees the layer
   (drawqa/layer.py: every item found on the drawing, with ids) and can list items, search the printed text and run a
   small calculation in a sandbox (no network, no file writes). It never sees the image: the picture is used once,
   when the layer is made. It must finish with
   answer(...) citing the item ids its answer rests on. An answer without evidence is not reported as detected or
   measured; a question the drawing does not answer gets "cannot determine" and why.
Tiers: read (printed on the sheet) / measured (computed from geometry at a known scale) / detected (counted from found
items) / estimated (rests on an assumption) / cannot determine.
"""
import json, math, os, subprocess, tempfile, time

TIERS = ("read", "measured", "detected", "estimated", "cannot determine")
MAX_TURNS = int(os.environ.get("DRAWQA_ANSWER_TURNS", "6"))

TOOLS = [
    {"type": "function", "function": {"name": "items", "description": "List the items of one class of the layer: id, box [x0,y0,x1,y1] in page pixels, area in m2 for outlines (when the scale is known), and the values read for it (label, tag, size...).",
     "parameters": {"type": "object", "properties": {"cls": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["cls"]}}},
    {"type": "function", "function": {"name": "find_text", "description": "Search the text printed on the sheet (case-insensitive substring). Returns matching texts with their boxes.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "python", "description": "Run Python over the layer and print results. Preloaded: layer (dict), M (metres per pixel or None), items(name) -> list, area_m2(item), center(item) -> (x, y), inside(item, room_item) -> bool, dist_m(a, b), length_m(item). No network, no file writes, 20 s.",
     "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "answer", "description": "Your final answer. value: a plain number (no unit or words inside it) when the answer is a quantity, otherwise a short text; names go in the explanation. unit: e.g. count, m2, m, or empty. tier: read | measured | detected | estimated | cannot determine. evidence: the ids of the items (or the texts) the answer rests on. explanation: one or two sentences on how you got it, including any assumption.",
     "parameters": {"type": "object", "properties": {"value": {}, "unit": {"type": "string"}, "tier": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}}, "explanation": {"type": "string"}},
                    "required": ["value", "tier", "explanation"]}}},
]

SYSTEM = """You answer one question about one construction drawing. You do NOT see the drawing: you get our structured
reading of it (the layer): classes of items (each with an id like "door#3"), who found them, the sheet's scale and its
printed text. Answer only from that data.
Rules:
- Count, add and measure with the tools (items, python, find_text); never estimate a number in your head.
- The trained detector is reliable on its classes; "Claude" items were found and checked by an agent on the drawing.
- If the layer does not contain what is asked, answer "cannot determine" and say exactly what is missing from the
  reading (for example "dishwashers were not identified on this sheet"). Do not guess from names alone.
- Areas and lengths need the scale. If the scale is marked as estimated or unknown, say so (tier "estimated").
- Walls (from the notes): "perimeter" of the walls, building or apartment = the OUTSIDE face of the walls; give that
  number and add the inside total in the explanation; cite the 'wall perimeter' items you used (face outside/inside). A room's perimeter = its inside face. "Total wall length" = the
  sum of wall centre lines (solid wall, corners from both sides); say which measure you give.
- Cite the ids your answer rests on in answer(evidence=...). Be brief. You have at most {turns} tool calls."""


def _summary(layer):
    L = [f"Sheet: {layer.get('kind') or 'drawing'}, page {layer.get('page', 0)}, {layer.get('width')} x {layer.get('height')} px.",
         f"Scale: {layer.get('m_per_px')} m per px ({layer.get('scale_source') or 'source unknown'})." if layer.get("m_per_px") else "Scale: unknown.",
         f"Reading: our trained models; Claude discovery {layer.get('discovery', {}).get('status', 'not run')}."]
    for n in layer.get("notes") or []:
        L.append("Note: " + n)
    L.append("Classes (name: count, found by, geometry):")
    for c in layer.get("classes", []):
        ex = next((it for it in c.get("items") or []), {})
        vals = {k: v for k, v in ex.items() if k not in ("id", "bbox", "polygon", "polyline", "point", "score") and isinstance(v, (str, int, float))}
        L.append(f"  {c['name']}: {c.get('count', len(c.get('items') or []))}, {c.get('source', '?')}, {c.get('geometry', '?')}" + (f", e.g. {json.dumps(vals)[:120]}" if vals else ""))
    t = layer.get("texts") or []
    L.append(f"Printed texts: {len(t)} (use find_text). Some: " + "; ".join(x["text"] for x in t[:60])[:1500])
    return "\n".join(L)


def _box(it):
    if it.get("bbox"):
        return it["bbox"]
    pts = it.get("polygon") or it.get("polyline")
    if pts:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return [min(xs), min(ys), max(xs), max(ys)]
    if it.get("point"):
        x, y = it["point"]
        return [x - 5, y - 5, x + 5, y + 5]
    return None


def _area_px(it):
    pts = it.get("polygon")
    if not pts:
        b = _box(it)
        return (b[2] - b[0]) * (b[3] - b[1]) if b else 0
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2


def _items(layer, cls, limit=60):
    c = next((c for c in layer.get("classes", []) if c["name"].lower() == cls.lower()), None)
    if c is None:
        names = [c["name"] for c in layer.get("classes", [])]
        return f"no class '{cls}'. Classes: {', '.join(names)}"
    M = layer.get("m_per_px")
    out = []
    for it in (c.get("items") or [])[: int(limit or 60)]:
        r = dict(id=it.get("id"), box=[round(v) for v in _box(it) or []])
        if it.get("polygon") and M:
            r["area_m2"] = round(_area_px(it) * M * M, 2)
        r.update({k: v for k, v in it.items() if k not in ("id", "bbox", "polygon", "polyline", "point") and isinstance(v, (str, int, float))})
        out.append(r)
    more = len(c.get("items") or []) - len(out)
    return json.dumps(out) + (f"\n(+{more} more)" if more > 0 else "")


PRELUDE = """import json, math
layer = json.load(open("layer.json"))
M = layer.get("m_per_px")
def items(name):
    return next((c.get("items") or [] for c in layer["classes"] if c["name"].lower() == name.lower()), [])
def _box(it):
    if it.get("bbox"): return it["bbox"]
    pts = it.get("polygon") or it.get("polyline")
    if pts: return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
    x, y = it["point"]; return [x, y, x, y]
def center(it):
    b = _box(it); return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
def area_m2(it):
    pts = it.get("polygon")
    if not pts:
        b = _box(it); a = (b[2] - b[0]) * (b[3] - b[1])
    else:
        a = abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2
    return a * M * M if M else None
def inside(it, room):
    x, y = center(it); P = room.get("polygon") or []
    c = False
    for i in range(len(P)):
        (x1, y1), (x2, y2) = P[i], P[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: c = not c
    return c
def dist_m(a, b):
    (x1, y1), (x2, y2) = center(a), center(b); d = math.hypot(x2 - x1, y2 - y1); return d * M if M else None
def length_m(it):
    p = it.get("polyline") or []
    d = sum(math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]) for i in range(1, len(p))); return d * M if M else None
"""


def _python(layer, code):
    with tempfile.TemporaryDirectory() as td:
        json.dump(layer, open(os.path.join(td, "layer.json"), "w"))
        open(os.path.join(td, "run.py"), "w").write(PRELUDE + "\n" + code)
        prof = os.path.join(td, "sb.sb")
        open(prof, "w").write(f'(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'
                              f'(allow file-write* (subpath "{os.path.realpath(td)}") (literal "/dev/null"))\n')
        py = os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "python")
        try:
            r = subprocess.run(["sandbox-exec", "-f", prof, py, "-I", "run.py"], cwd=td, capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            return "timed out after 20 s"
        return ((r.stdout or "") + (("\nERROR: " + r.stderr[-1500:]) if r.returncode else ""))[:4000] or "(no output)"


STOP = {"how", "many", "much", "what", "which", "where", "is", "are", "there", "the", "a", "an", "of", "on", "in", "this", "that",
        "drawn", "shown", "plan", "floor", "drawing", "sheet", "total", "number", "count", "do", "does", "we", "have", "and", "or",
        "to", "for", "with", "be", "it", "its", "all", "any", "unit", "units", "item", "items", "type", "kind"}


def _words(text):
    import re
    ws = re.findall(r"[a-zäöüåéèàç]+", str(text).lower().replace("_", " "))
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in ws if w not in STOP and len(w) > 2}


def _layer_classes_for(layer, question):
    """Classes found by the Claude step whose name shares a word with the question (wardrobe -> wardrobe_cell)."""
    q = _words(question)
    return [c["name"] for c in layer.get("classes", []) if c.get("source") == "Claude" and q & _words(c["name"])]


def _agent(layer, question, engine_note=None, backend=None):
    from . import llm
    text = _summary(layer) + ("\n\n" + engine_note if engine_note else "") + "\n\nQuestion: " + question
    msgs = [{"role": "system", "content": SYSTEM.format(turns=MAX_TURNS)}, {"role": "user", "content": text}]
    ids = {it.get("id"): (c["name"], _box(it)) for c in layer.get("classes", []) for it in c.get("items") or []}
    log = dict(turns=0, seconds=0.0, tokens=0)
    for turn in range(MAX_TURNS + 1):
        if turn == MAX_TURNS:
            msgs.append({"role": "user", "content": "No more tool calls: call answer now with what you have."})
        r = llm.chat(msgs, tools=TOOLS, max_tokens=3000, backend_name=backend)
        log["turns"] += 1; log["seconds"] += r.get("_seconds", 0); log["tokens"] += (r.get("usage") or {}).get("total_tokens", 0)
        m = r["choices"][0]["message"]
        calls = m.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": m.get("content") or "", **({"tool_calls": calls} if calls else {})})
        if not calls:
            msgs.append({"role": "user", "content": "Use the tools, and finish with answer(...)."})
            continue
        for c in calls:
            name = c["function"]["name"]
            try:
                a = json.loads(c["function"].get("arguments") or "{}")
            except Exception:
                a = {}
            if name == "answer":
                return a, ids, log
            if name == "items":
                out = _items(layer, a.get("cls", ""), a.get("limit", 60))
            elif name == "find_text":
                q = str(a.get("query", "")).lower()
                hits = [t for t in layer.get("texts", []) if q and q in t["text"].lower()][:40]
                out = json.dumps(hits) if hits else "no printed text contains that"
            elif name == "python":
                out = _python(layer, a.get("code", ""))
            else:
                out = f"unknown tool {name}"
            msgs.append({"role": "tool", "tool_call_id": c.get("id"), "content": out})
    return None, ids, log


def ask(path, question, page=0, discover=True, backend=None):
    from . import layer as LY
    t0 = time.time()
    src, png, kind = (None, None, None)
    try:
        src, png, kind = LY.page_source(path, page)
    except Exception as e:
        return dict(answer=None, tier="cannot determine", basis=f"the file could not be read: {e}", route="none", seconds=round(time.time() - t0, 1))
    use_engine = png is None or os.environ.get("DRAWQA_USE_ENGINE") == "1"
    if not use_engine:
        # every question about a drawing goes straight to the answer agent, which works from the reading (the layer);
        # the rule engine is kept for building models (no picture) and, with DRAWQA_USE_ENGINE=1, for comparison
        L = LY.ingest(path, page, discover=discover)
        return _agent_answer(L, question, None, backend, t0)
    # 1. the engine, when it knows the question (building models always go here)
    try:
        from .solve import solve
        r = solve(src or path, question, generic=True)
        known = (r.get("intent") or {}).get("intent") not in (None, "unknown") and r.get("answer") is not None
    except Exception as e:
        r, known = {"error": str(e)}, False
    h = r.get("honesty") or {}
    if png is None:  # a building model: the engine is the only reader
        return dict(answer=r.get("answer"), unit=(r.get("intent") or {}).get("unit"), tier=h.get("tier"), basis=h.get("basis"),
                    evidence=r.get("evidence", []), route="engine", seconds=round(time.time() - t0, 1))
    L = LY.ingest(path, page, discover=discover)  # cached after the first time (normally read when the file arrives)
    # second witness for the routing: if the layer has a class named like the question that the engine did not use,
    # the engine probably read the question as something else ("wardrobe units" as storage rooms): the agent answers
    better = [n for n in _layer_classes_for(L, question) if n != (r.get("intent") or {}).get("cls")]
    it = r.get("intent") or {}
    if known and not better and it.get("intent") in ("count_openings", "count_fixtures"):
        # counts come from the checked reading, not from the detector's raw output: the Claude step may have removed
        # false hits (a doorway with no door, a "window" on a pipe crossing)
        cls, raw = it.get("cls"), r.get("answer")
        c = next((c for c in L.get("classes", []) if c["name"] == cls), None)
        checked = L.get("discovery", {}).get("status") == "done"
        if c is not None:
            items = c.get("items") or []
            basis = f"{len(items)} {cls}(s) in the reading of this drawing, found by the {c.get('source')}" + (", checked on the drawing by Claude" if checked else "")
            if isinstance(raw, int) and raw > len(items):
                basis += f"; the check removed {raw - len(items)} of the detector's {raw} candidates as not drawn"
            return dict(answer=len(items), unit="count", tier="detected", basis=basis, route="layer",
                        evidence=[dict(id=x.get("id"), cls=cls, bbox=_box(x)) for x in items], seconds=round(time.time() - t0, 1))
        if checked:
            sf = L.get("start_layer_file") or ""
            n0 = sum(len(c0.get("items") or []) for c0 in json.load(open(sf)).get("classes", []) if c0["name"] == cls) if os.path.exists(sf) else 0
            if n0:
                return dict(answer=0, unit="count", tier="detected", route="layer", evidence=[], seconds=round(time.time() - t0, 1),
                            basis=f"no {cls} is drawn: the detector's {n0} candidate(s) were checked on the drawing and rejected")
    if known and not better:
        return dict(answer=r.get("answer"), unit=(r.get("intent") or {}).get("unit"), tier=h.get("tier"), basis=h.get("basis"),
                    evidence=r.get("evidence", []), route="engine", seconds=round(time.time() - t0, 1))
    note = (f"The rule-based engine read this question as {json.dumps(r.get('intent'))} and answered {r.get('answer')!r} "
            f"({h.get('basis')}); check whether that fits the question before relying on it.") if known else None
    # 2. the answer agent over the layer
    return _agent_answer(L, question, note, backend, t0)


def _agent_answer(L, question, note, backend, t0):
    """The answer agent over the reading: Kimi (default) or Qwen through drawqa/llm.py, or Claude Haiku through headless
    Claude Code (backend="haiku"). Normalises the reply and checks its evidence."""
    if backend == "haiku":
        try:
            a, ids, log = _claude_cli_agent(L, question, note, "haiku")
        except Exception as e:
            return dict(answer=None, tier="cannot determine", basis=f"no answer model available ({e})", route="agent", seconds=round(time.time() - t0, 1))
        model = "Claude Haiku (Claude Code)"
    else:
        model = "Qwen3-VL-32B (Modal)" if (backend or os.environ.get("DRAWQA_ANSWER_LLM")) == "qwen" else "Kimi K3.1 Pro (Modal)"
        try:
            a, ids, log = _agent(L, question, note, backend)
        except Exception as e:  # the model is down: say so, never guess
            return dict(answer=None, tier="cannot determine", basis=f"no answer model available ({e})", route="agent", seconds=round(time.time() - t0, 1))
    if a is None:
        return dict(answer=None, tier="cannot determine", basis="the answer agent ran out of steps", route="agent", log=log, seconds=round(time.time() - t0, 1))
    v = a.get("value")
    if isinstance(v, dict):  # {"room": "terrace", "area_m2": 21.81} -> 21.81, the rest into the explanation
        nums = [(k, x) for k, x in v.items() if isinstance(x, (int, float)) and not isinstance(x, bool)]
        other = ", ".join(f"{k}: {x}" for k, x in v.items() if not (isinstance(x, (int, float)) and not isinstance(x, bool)))
        if nums:
            a["value"] = nums[0][1]
            a["explanation"] = (other + ". " if other else "") + str(a.get("explanation") or "")
    elif isinstance(v, str):
        import re
        m = re.fullmatch(r"\s*(-?\d+(?:[.,]\d+)?)\s*([a-zA-Z²/ ]*)\s*", v)
        if m:  # "21.81 m²" -> 21.81 with unit m²
            a["value"] = float(m.group(1).replace(",", ".")) if "." in m.group(1) or "," in m.group(1) else int(m.group(1))
            a["unit"] = a.get("unit") or m.group(2).strip() or None
    tier = str(a.get("tier", "")).lower().strip()
    tier = tier if tier in TIERS else "estimated"
    ev = [dict(id=i, cls=ids[i][0], bbox=ids[i][1]) for i in a.get("evidence") or [] if i in ids]
    notes = []
    if tier in ("detected", "measured") and not ev:
        tier = "estimated"
        notes.append("no item of the layer was cited as evidence")
    return dict(answer=a.get("value"), unit=a.get("unit"), tier=tier, basis=a.get("explanation"), evidence=ev, notes=notes,
                route="agent", model=model, log=log, seconds=round(time.time() - t0, 1))


CLI_TASK = """{system}

The reading of the drawing is in layer.json in this folder; helper.py loads it (from helper import *) and gives:
layer, M (metres per pixel or None), items(name), area_m2(item), center(item), inside(item, room), dist_m(a, b),
length_m(item). texts = layer["texts"]. Use Bash to run python ({python}) with them. Do not open any other file.

{summary}
{note}
Question: {question}

Finish by writing answer.json in this folder: {{"value": ..., "unit": "...", "tier": "read|measured|detected|estimated|cannot determine", "evidence": ["id", ...], "explanation": "..."}}"""


def _claude_cli_agent(L, question, note, model):
    """The answer agent as a headless Claude Code run (no API key: the local Claude Code login). It gets the same
    reading and helpers as the Kimi agent, a Python sandbox folder, and must write answer.json."""
    import shutil
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    ids = {it.get("id"): (c["name"], _box(it)) for c in L.get("classes", []) for it in c.get("items") or []}
    with tempfile.TemporaryDirectory() as td:
        json.dump(L, open(os.path.join(td, "layer.json"), "w"))
        open(os.path.join(td, "helper.py"), "w").write(PRELUDE)
        py = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "python"))
        prompt = CLI_TASK.format(system=SYSTEM.format(turns=MAX_TURNS), python=py, summary=_summary(L), note=note or "", question=question)
        t0 = time.time()
        r = subprocess.run([exe, "-p", prompt, "--model", model, "--output-format", "json", "--allowedTools", "Bash", "Read", "Write",
                            "--add-dir", td], cwd=td, capture_output=True, text=True, timeout=300)
        log = dict(turns=None, seconds=round(time.time() - t0, 1))
        try:
            meta = json.loads(r.stdout or "{}")
            log.update(turns=meta.get("num_turns"), cost_usd=meta.get("total_cost_usd"))
        except Exception:
            pass
        f = os.path.join(td, "answer.json")
        a = json.load(open(f)) if os.path.exists(f) else None
    return a, ids, log
