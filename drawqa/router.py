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
    ("count_rooms_class", "balcony", r"balcon(y|ies)|parveke"),
    ("count_floors", None, r"floors \(storeys\)|kerrosta"),
]
COUNT_WORDS = r"How many|Kuinka monta|Wie viele|Combien|Quant[ie]|Mitu|Hvor mange"
AREA_WORDS = r"area|Fläche|surface|superficie|pind|areal"


def parse(question):
    q = question.strip()
    quoted = quoted_strings(q)
    is_count = re.search(COUNT_WORDS, q, re.I) is not None
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

    # drawing questions
    if is_count and (m := re.search(r"labelled|denominazione|Bezeichnung|désign", q)) and quoted:
        return dict(intent="count_rooms_label", label=quoted[0])
    if is_count:
        for intent, cls, rx in COUNT_RULES:
            if re.search(rx, q, re.I):
                return dict(intent=intent, cls=cls)
    if is_area:
        if re.search(r"apartment|appartamento|Wohnung|appartement", q, re.I) and (m := re.search(r"\b([A-Z])\b(?! m)", q.split("m²")[0].replace("WC", ""))):
            return dict(intent="unit_area", unit=m.group(1))
        if (m := ROOM_NUMBER.search(q)) and re.search(r"room|locale|Raum|pièce", q, re.I):
            return dict(intent="room_area", number=m.group(1))
        if quoted and re.search(r"labelled|denominati|Bezeichnung|désign|named", q, re.I):
            return dict(intent="label_area", label=quoted[0])
        if re.search(r"total|gesamte|totale", q, re.I):
            return dict(intent="floor_area")
    return dict(intent="unknown")
