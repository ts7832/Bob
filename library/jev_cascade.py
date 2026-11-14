"""Cascade router: the engine's pattern router first; when it cannot read a question, the Jev-style router
(jev/modal_router.py: multilingual-e5-small, calibrated) says what is asked, and rules pull out the specifics
(room name and number, storey, apartment letter, room label) from the question's own quoted strings.

    from library import jev_cascade
    jev_cascade.install("/path/to/router1")       # patches drawqa.router.parse for the whole process
    jev_cascade.last()                            # how the last question was read (for the audit trail)

The router only chooses the intent; every number still comes from the engine's deterministic tools.
MIN_PROB: below it the Jev-style router's reading is not used (the question stays "unknown": cannot determine, or Opus
when a language model is configured).
"""
import os, re, threading

MIN_PROB = float(os.environ.get("JEV_MIN_PROB", "0.5"))
_state = threading.local()
_model = {}


def _load(path):
    if _model:
        return _model
    import torch
    from transformers import AutoModel, AutoTokenizer
    torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))
    tok = AutoTokenizer.from_pretrained(os.path.join(path, "encoder"))
    enc = AutoModel.from_pretrained(os.path.join(path, "encoder")).eval()
    h = torch.load(os.path.join(path, "head.pt"), map_location="cpu")
    head = torch.nn.Linear(enc.config.hidden_size, len(h["labels"]))
    head.load_state_dict(h["head"])
    _model.update(tok=tok, enc=enc, head=head.eval(), labels=h["labels"], t=h["temperature"])
    return _model


def predict(question, path=None):
    """-> (label, probability, top-3 [(label, p)])"""
    import torch
    m = _load(path or os.environ.get("JEV_ROUTER", ""))
    with torch.no_grad():
        b = m["tok"](["query: " + question], padding=True, truncation=True, max_length=96, return_tensors="pt")
        hs = m["enc"](**b).last_hidden_state
        mask = b["attention_mask"].unsqueeze(-1).float()
        p = (m["head"]((hs * mask).sum(1) / mask.sum(1)) / m["t"]).softmax(1)[0]
    top = sorted(((m["labels"][i], float(v)) for i, v in enumerate(p)), key=lambda x: -x[1])[:3]
    return top[0][0], top[0][1], top


QUOTES = re.compile(r"[\"'“”„«»‚‘’]\s*([^\"'“”„«»‚‘’]+?)\s*[\"'“”„«»‚‘’]")


def _quoted(q):
    return [s.strip() for s in QUOTES.findall(q) if s.strip()]


def _name_number(s):
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", s)
    return (m.group(1).strip() or None, m.group(2).strip()) if m else (s, None)


def to_intent(label, q, ifc=None):
    """The engine's intent dict for a meaning, with the specifics taken from the question. ifc: True for a BIM model
    question (model name or storey quoted, or the sheet is a BIM plan); None = guess from the wording."""
    qs = _quoted(q)
    model_word = re.search(r"(?i)\b(model|modell|modèle|modelo|modelul|mallissa|malli|modellen)\b", q)
    if ifc is None:      # a quoted storey or model name means a building-model question (Swiss and scan questions quote room labels only)
        ifc = bool(model_word) or len(qs) >= 2 or (len(qs) == 1 and label not in ("count:rooms_of_a_type", "area:rooms_of_a_type", "area:one_room"))
    looks_like_model = lambda x: bool(re.search(r"(?i)model|modell|project|projekt|example|building|haus| - |\(", x)) or len(x) > 28
    storey = None
    cands = [x for x in qs if not looks_like_model(x)]        # a quoted model name is not a storey
    if ifc and cands:
        storey = cands[-1]
    kind, _, what = label.partition(":")
    if label == "other":
        return None
    if kind == "count" and what in ("door", "window"):
        return dict(intent="ifc_count", cls=what, storey=storey) if ifc else dict(intent="count_openings", cls=what)
    if kind == "count" and what in ("wall", "stair", "column", "beam"):
        return dict(intent="ifc_count", cls=what, storey=storey)
    if label == "count:all_rooms":
        return dict(intent="ifc_count", cls="space", storey=storey)
    if label.startswith("count:fixtures:"):
        f = label.split(":")[-1]
        if ifc or f == "any":
            return dict(intent="ifc_count", cls="sanitary", storey=storey)
        return dict(intent="count_fixtures", cls=f.replace("shower_or_bathtub", "shower+bathtub"))
    if label == "count:rooms_of_a_type" and qs:
        return dict(intent="count_rooms_label", label=qs[0])
    if label == "count:apartments":
        return dict(intent="count_units")
    if label == "count:floors":
        return dict(intent="count_floors")
    if label == "area:one_room":
        if ifc and qs:
            name, number = _name_number(qs[0])
            return dict(intent="ifc_room_area", name=name, number=number, storey=qs[1] if len(qs) > 1 and not looks_like_model(qs[1]) else None)
        m = re.search(r"\b(\d+(?:\.\d+)+)\b", q)
        return dict(intent="room_area", number=m.group(1)) if m else None
    if label == "area:whole_floor":
        return dict(intent="ifc_storey_area", storey=storey) if ifc else dict(intent="floor_area")
    if label == "area:apartment":
        m = re.search(r"(?i)\b(?:apartment|wohnung|appartement|logement|asunto|asunnon|lägenhet|lägenheten|unit)\s+([A-Z0-9]{1,3})\b", q)
        return dict(intent="unit_area", unit=m.group(1)) if m else None
    if label == "area:rooms_of_a_type" and qs:
        return dict(intent="label_area", label=qs[0])
    if kind in ("length", "volume", "area") and what in ("wall", "slab"):    # the engine's own quantity names
        return dict(intent="ifc_quantity", quantity={"length": "length", "volume": "net volume", "area": "net side area"}[kind], cls=what, storey=storey)
    return None


def install(path=None):
    """Patch drawqa.router.parse: patterns first, the Jev-style router when they find nothing."""
    from drawqa import router
    if getattr(router.parse, "_cascade", False):
        return
    if path:
        os.environ["JEV_ROUTER"] = path
    orig = router.parse

    def parse(question, *a, **kw):
        r = orig(question, *a, **kw)
        if r.get("intent") != "unknown":
            _state.last = dict(by="pattern router", intent=r)
            return r
        label, p, top = predict(question)
        it = to_intent(label, question) if p >= MIN_PROB else None
        _state.last = dict(by="jev router" if it else "nobody", label=label, probability=round(p, 4),
                           top=[(l, round(v, 4)) for l, v in top], intent=it or {"intent": "unknown"},
                           note=None if it else ("below the confidence threshold" if p < MIN_PROB else f"'{label}': no usable specifics in the wording"))
        return it or r
    parse._cascade = True
    router.parse = parse


def last():
    return getattr(_state, "last", None)
