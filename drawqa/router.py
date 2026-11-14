"""Question -> structured intent.

Keyword/regex router covering the dataset's templates in EN/FI/DE/FR/IT/ET/DA.
This is the piece an LLM replaces later (same output contract: {"intent": ..., **params}).
"""
import re

_QUOTE_PATTERNS = [r"«\s*(.+?)\s*»", r"„(.+?)“", r"“(.+?)”", r"\"(.+?)\"", r"(?:^|\s)'(.+)'(?=\s|[?,.]|$)"]


def quoted_strings(q):
    """Names in quotes; greedy for single quotes so apostrophes inside names survive ('Cage d'escalier')."""
    for p in _QUOTE_PATTERNS:
        found = re.findall(p, q)
        if found:
            return [f.strip() for f in found]
    return []
ROOM_NUMBER = re.compile(r"\b([A-Z]?\d+\.\d+)\b")

# (intent, class, regex) -- order matters: specific before generic
COUNT_RULES = [
    ("count_rooms_class", "bathroom", r"bathrooms? or washrooms|kylpy- tai pesuhuone"),
    ("count_rooms_class", "wc_room", r"separate WC|erillis\w* WC"),
    ("count_fixtures", "shower+bathtub", r"showers and bathtubs|suihkua ja kylpyammetta"),
    ("count_fixtures", "shower", r"\bshowers?\b|Dusche|douches?|docce|doccia"),
    ("count_fixtures", "bathtub", r"bathtubs?|Badewanne|baignoires?|vasche|vasca"),
    ("count_fixtures", "wc", r"toilet bowls|WC seats|toilets \(WC\)|WC-istuin|\bWCs?\b|\bWC\b"),
    ("count_fixtures", "sink", r"sinks|wash ?basins|altaita|allasta|Lavabos?|lavabi"),
    ("count_openings", "window", r"windows?|ikkun\w*|Fenster|fenêtres?|finestre|akent|vinduer"),
    ("count_openings", "door", r"\bdoors?\b|\bov(ea|i)\b|Türen|portes|porte\b|\buks\b|\bust\b|døre"),
    ("count_units", None, r"apartments|residential units|Wohnungen|appartements|appartamenti|logements"),
    ("count_rooms_class", "sauna", r"\bsauna"),
    ("count_rooms_class", "kitchen", r"kitchens?|keittiö"),
    ("count_rooms_class", "bedroom", r"bedrooms?|makuuhuone"),
    ("count_rooms_class", "bathroom", r"\bbathrooms?\b"),  # plain "how many bathrooms" (the template above says "or washrooms")
    ("count_rooms_class", "balcony", r"balcon(y|ies)|parveke"),
    ("count_floors", None, r"floors \(storeys\)|kerrosta"),
    # room types beyond the dataset's templates (found by the detector on images)
    ("count_rooms_class", "living_room", r"living ?rooms?|lounges?|olohuone"),
    ("count_rooms_class", "dining", r"dining|ruokailu"),
    ("count_rooms_class", "entry", r"entrance|entry|hallways?|\bhalls?\b|lobb(y|ies)|eteinen|eteis"),
    ("count_rooms_class", "storage", r"storage|closets?|wardrobes?|varasto|vaatehuone"),
    ("count_rooms_class", "utility", r"utility|laundry|technical rooms?|kodinhoito"),
    ("count_rooms_class", "dressing_room", r"dressing rooms?|changing rooms?|pukuhuone"),
    ("count_rooms_class", "terrace", r"terraces?|porch(es)?|patios?|verandas?|terassi|kuisti"),
    ("count_rooms_class", "garage", r"garages?|car ?ports?|autotalli"),
    ("count_floors", None, r"\bfloors\b|storeys|stories"),
]
COUNT_WORDS = r"How many|Kuinka monta|Wie viele|Combien|Quant[ie]|Mitu|Hvor mange"
AREA_WORDS = r"area|Fläche|surface|superficie|pind|areal"
EXCLUDE_WORDS = r"not counting|excluding|except|without|ohne|außer|sauf|sans|escluso|tranne|senza"
EXCLUDE_AFTER = r"[^,;]*\b(lukuun ottamatta|ei lukien)"  # Finnish puts the exclusion after what it excludes
# words that name another thing of the same kind but are too loose to count on their own ("toilets" may be rooms)
ALSO_NAMED = {"count_fixtures": r"\btoilets?\b|lavator(y|ies)|water closets?|urinals?|bidets?",
              "count_rooms_class": r"\bbathrooms?\b|washrooms?|\btoilets\b|\bWC rooms\b"}


ARITHMETIC = (r"\b(minus|plus|times|multipl\w*\s+by|divided\s+by|subtract\w*|deduct\w*|add(ing)?\s+\d+|miinus|vähennä\w*|"
              r"weniger|abzüglich|moins|meno)\b|(?<![\w.\-/'\"’”])[-+×÷*]\s*\d|\d\s*%|\bper\s?cent\b|prosent\w*|Prozent")
# (a minus right after a foot or inch mark is a US dimension, 3'-0", not arithmetic)


GENERIC_PLACE = (r"(?:whole\s+|entire\s+)?(?:floor|building|plan|floor\s*plan|apartment|flat|unit|house|storey|story|level|sheet|"
                 r"drawing|dwelling|model)s?|(?:all\s+)?(?:indoor\s+|numbered\s+|interior\s+)?rooms")


def names_specific_room(question):
    """The room phrase a question names ("the total area OF THE SECONDARY CLASSROOMS", "skirting FOR THE LIBRARY"), or
    None when it names none or only the whole floor / plan / building."""
    q = re.sub(r"\(.*?\)", " ", question)
    m = re.search(r"\b(?:of|for|in)\s+(?:the|all\s+the)\s+([A-Za-z][\w\-' ]{2,40}?)(?=\s+(?:on|in|of|at|for|need|needs|require|requires|have|has)\b|\s*[?,.]|\s*$)", q, re.I)
    if not m or re.fullmatch(GENERIC_PLACE, m.group(1).strip(), re.I):
        return None
    return m.group(1).strip()


NOT_A_ROOM = {"floor", "building", "plan", "apartment", "unit", "whole", "total", "entire", "rooms", "drawing", "sheet",
              "model", "house", "flat", "ceiling", "walls", "wall", "skirting", "board", "boards", "area", "perimeter",
              "length", "height", "scale", "doors", "door", "windows", "window", "storey", "level", "plans", "same", "first",
              "second", "third", "ground", "basement", "attic", "drawings", "question", "answer", "tiles", "paint"}


def room_phrase(question):
    """The room words a question is about: "the area OF THE MULTI-PURPOSE HALL", "how many SECONDARY CLASSROOMS are
    there", "skirting does THE LIBRARY need". None when it names no particular room."""
    p = names_specific_room(question)
    if not p:
        m = re.search(r"\bhow many\s+([a-z][\w\- ]{2,40}?)\s+(?:are|is|do|does|can|on|in|there|exist)\b", question, re.I)
        p = m.group(1) if m and m.group(1).lower() not in NOT_A_ROOM else None
    if not p:
        m = [w for w in re.findall(r"\bthe\s+([a-z][\w\-]{3,30})\b", question, re.I) if w.lower() not in NOT_A_ROOM]
        p = m[0] if m else None
    return p


def names_matching(phrase, names):
    """Printed room names that contain every word of the phrase (plural-tolerant): "secondary classrooms" ->
    "Secondary Classroom", "Secondary Classroom (Severe Needs)"; "multi-purpose hall" -> "Multi-Purpose Hall"."""
    sing = lambda w: w[:-1] if len(w) > 3 and w.endswith("s") else w
    pw = {sing(w) for w in re.findall(r"[a-z]+", (phrase or "").lower())} - {"the", "all", "and", "of", "room"} \
        if phrase and len(re.findall(r"[a-z]+", phrase.lower())) > 1 else \
        {sing(w) for w in re.findall(r"[a-z]+", (phrase or "").lower())} - {"the", "all", "and", "of"}
    return [n for n in names if pw and pw <= {sing(w) for w in re.findall(r"[a-z]+", str(n).lower())}]


def parse(question):
    q = question.strip()
    if re.search(ARITHMETIC, q, re.I):  # "total area minus 10": the pattern tools cannot do the arithmetic; the model passes it on
        return dict(intent="unknown")
    if re.search(r"\b(?:in|of|for)\s+(?:the\s+)?(?:unit|apt\.?|apartment)\s+[A-Z]?\d+[A-Z]?\b", q, re.I) and \
            re.search(r"living|kitchen|bed|bath|toilet|closet|balcon|entry|dining|room", q, re.I):
        return dict(intent="unknown")  # "the living room in unit U212": a room in one unit; the model passes the unit
    quoted = quoted_strings(q)
    # "how many metres of skirting", "wie viele Quadratmeter": a measurement, not a count of things
    is_count = re.search(COUNT_WORDS, q, re.I) is not None and not re.search(
        rf"(?:{COUNT_WORDS})\s+(?:\w+\s+)?(?:square\s+|sq\.?\s+|running\s+|linear\s+|cubic\s+)?"
        r"(?:met(?:er|re)s?|m2|m²|m3|cm2?|cm²|mm2?|mm²|centimet\w*|millimet\w*|squared?\b|sq\b|"
        r"feet|foot|ft|yards?|litres?|liters?|kilo\w*|kg|tons?|tonnes?|neliö\w*|metri\w*|"
        r"quadratmeter\w*|kubikmeter\w*|meter\b|mètres?|mètres?\s+carrés?|metri\b|metri\s+quadri|ruutmeet\w*|meet\w*|kvadratmeter\w*)", q, re.I)
    is_area = re.search(AREA_WORDS, q, re.I) is not None

    # IFC-style questions name storeys/models in quotes
    if m := re.search(r"floor plan of ['„«]\s*(.+?)\s*['“»]|korruse „(.+?)“|plantegningen for '(.+?)'|im Grundriss „(.+?)“", q):
        storey = next(g for g in m.groups() if g)
        for intent, cls, rx in COUNT_RULES:
            if re.search(rx, q, re.I):
                return dict(intent="ifc_count", cls=cls or "space", storey=storey)
        if re.search(r"rooms|spaces|ruum|rum", q, re.I):
            return dict(intent="ifc_count", cls="space", storey=storey)
        if re.search(r"walls|sein|vægge", q, re.I): return dict(intent="ifc_count", cls="wall", storey=storey)
        if re.search(r"columns|post|søjler", q, re.I): return dict(intent="ifc_count", cls="column", storey=storey)
        if re.search(r"stairs|trep|trapper", q, re.I): return dict(intent="ifc_count", cls="stair", storey=storey)
    if re.search(r"in the model|mudelis|i modellen|das Modell", q, re.I) and is_count:
        for word, cls in [("door|uks|\\bust\\b|døre|Türen", "door"), ("window|aken|vinduer|Fenster", "window"), ("room|space|ruum|\\brum\\b|Räume", "space"),
                          ("wall|sein|vægge|Wände", "wall"), ("beam|tala|bjælker|Träger", "beam"), ("stair|trep|trapper|Treppen", "stair"),
                          ("column|post|søjler", "column"), ("sanitary", "sanitary")]:
            if re.search(word, q, re.I):
                return dict(intent="ifc_count", cls=cls, storey=None)
    if m := re.search(r"net floor area of the room '(.+) \((.+)\)' on '(.+?)'|ruumi „(.+) \((.+)\)“ netopõrandapind korrusel „(.+?)“|rummet '(.+) \((.+)\)' på '(.+?)'", q):
        g = [x for x in m.groups() if x]
        return dict(intent="ifc_room_area", name=g[0], number=g[1], storey=g[2])
    if m := re.search(r"total net floor area of all rooms \(spaces\) on '(.+?)'|kõigi ruumide netopõrandapind kokku korrusel „(.+?)“", q):
        return dict(intent="ifc_storey_area", storey=next(x for x in m.groups() if x))
    if quoted and re.search(r"samlede nettoareal af alle rum", q, re.I):
        return dict(intent="ifc_storey_area", storey=quoted[0])
    qty = next((name for rx, name in [(r"net volume|nettovolumen|Netto-Volumen", "net volume"),
                                      (r"net side area|nettosideareal|Seitenfläche", "net side area"),
                                      (r"total length|længde|Wie lang", "length")] if re.search(rx, q, re.I)), None)
    elem = "wall" if re.search(r"walls|vægge|Wände", q, re.I) else "slab" if re.search(r"slabs|dæk|Decken", q, re.I) else None
    if qty and elem and quoted:
        return dict(intent="ifc_quantity", quantity=qty, cls=elem, storey=quoted[0])
    if qty == "length" and elem == "wall":  # no storey named: a plain drawing, walls from the segmentation model
        return dict(intent="wall_length")

    # drawing questions
    if is_count and (m := re.search(r"labelled|denominazione|Bezeichnung|désign", q)) and quoted:
        return dict(intent="count_rooms_label", label=quoted[0])
    if is_count:
        for intent, cls, rx in COUNT_RULES:
            if m := re.search(rx, q, re.I):
                # a second thing of the same kind named too ("doors and windows together", "toilets and sinks"): one
                # count cannot answer it, so leave it to the language model, which can plan two counts and add them.
                # Bracketed qualifiers and exclusions ("not counting separate WC rooms") are not a second thing, and
                # neither is a word of another kind ("entry doors", "doors in the apartments").
                rest = re.sub(r"\(.*?\)", " ", q[:m.start()] + " " + q[m.end():])
                rest = re.split(EXCLUDE_WORDS, re.sub(EXCLUDE_AFTER, " ", rest, flags=re.I), flags=re.I)[0]
                if (any(i2 == intent and c2 != cls and re.search(rx2, rest, re.I) for i2, c2, rx2 in COUNT_RULES)
                        or (intent in ALSO_NAMED and re.search(ALSO_NAMED[intent], rest, re.I))):
                    return dict(intent="unknown")
                return dict(intent=intent, cls=cls)
    if is_area:
        if re.search(r"\bwalls?\b|\bWand|Wände|seinä|\bmurs?\b|pareti|parete|\bvægge|\bseina", q, re.I):
            return dict(intent="unknown")  # wall surface, not floor area: left to the language model (wall_area)
        if re.search(r"apartment|appartamento|Wohnung|appartement", q, re.I) and (m := re.search(r"\b([A-Z])\b(?! m)", q.split("m²")[0].replace("WC", ""))):
            if len(set(re.findall(r"\b([A-Z])\b(?! m)", q.split("m²")[0].replace("WC", "")))) > 1:
                return dict(intent="unknown")  # "apartments A and B": several apartments, left to the language model
            return dict(intent="unit_area", unit=m.group(1))
        if (m := ROOM_NUMBER.search(q)) and re.search(r"room|locale|Raum|pièce", q, re.I):
            return dict(intent="room_area", number=m.group(1))
        if quoted and re.search(r"labelled|denominati|Bezeichnung|désign|named", q, re.I):
            return dict(intent="label_area", label=quoted[0])
        for intent, cls, rx in COUNT_RULES:  # "area of the bedrooms": rooms known by type (images)
            # room words inside brackets are qualifiers ("... (excluding balconies, terraces ...)"), not the subject
            if intent == "count_rooms_class" and cls != "sauna" and re.search(rx, re.sub(r"\(.*?\)", "", q), re.I):
                return dict(intent="class_area", cls=cls)
        if re.search(r"total|gesamte|totale", q, re.I):
            if names_specific_room(q):  # "total area of the secondary classrooms": named rooms, not the whole floor
                return dict(intent="unknown")
            return dict(intent="floor_area")
    if re.search(r"\b(scale|ma(ss|ß)stab|[ée]chelle|scala|mittakaava)\b", q, re.I) and not is_count and not is_area:
        return dict(intent="scale")
    return dict(intent="unknown")
