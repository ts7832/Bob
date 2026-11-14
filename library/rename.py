"""Friendly names for library sheets: "Esplanades · Level 2" instead of a file name.

    python library/rename.py            # every sheet in the Demo workspace
"""
import json, os, re, urllib.parse, urllib.request

URL = os.environ.get("BOB_LIBRARY", "https://oliver-universityapps--bob-library-web.us-east.modal.run")
PW = os.environ.get("BOB_PASSWORD", "123456789")
BUILDINGS = {"bsc-esplanades": "Esplanades", "bsc-molio": "Molio", "bimspeed-warmond": "Warmond", "bimspeed-vitoria": "Vitoria",
             "tuwien-escape-route": "TU Wien", "rwth-digitalhub": "DigitalHub", "kit-fzk-haus": "FZK-Haus", "kit-smiley-west": "Smiley West"}
STOREYS = {"fundament": "Foundation", "klder": "Basement", "kaelder": "Basement", "grundplan": "Ground floor", "basement": "Basement",
           "entrance": "Entrance level", "attic": "Attic", "cta": "Site plan", "ground-level": "Ground floor"}


def friendly(file_name):
    stem = os.path.splitext(file_name or "")[0]
    m = re.match(r"site(\d+)_bldg(\d+)_floor(\d+)$", stem)
    if m:
        return f"Swiss plan · site {m.group(1)}, floor {m.group(3)}"
    m = re.match(r"(.+?)_.*_plan-\d+-(.+)$", stem) or re.match(r"(.+?)_plan-\d+-(.+)$", stem)
    if not m:
        return None
    b = next((v for k, v in BUILDINGS.items() if m.group(1).startswith(k)), m.group(1).split("-")[-1].title())
    s = m.group(2)
    if s in STOREYS:
        s = STOREYS[s]
    elif re.fullmatch(r"(\d+)-(korrus|sal|level)", s):
        s = "Level " + s.split("-")[0]
    elif re.fullmatch(r"intermediate-level-(\d+)", s):
        s = "Mezzanine " + s.rsplit("-", 1)[1]
    else:
        s = s.replace("-", " ").title()
    return f"{b} · {s}"


def call(method, path, **params):
    req = urllib.request.Request(URL + path + ("?" + urllib.parse.urlencode(params) if params else ""), method=method, headers={"x-bob-password": PW})
    return json.load(urllib.request.urlopen(req, timeout=60))


for p in call("GET", "/api/projects"):
    for s in call("GET", f"/api/projects/{p['id']}")["sheets"]:
        n = friendly(s["file_name"])
        if n and n != s["name"]:
            call("PATCH", f"/api/sheets/{s['id']}", name=n)
            print(f"{s['id']:3d}  {s['file_name'][:52]:52s} -> {n}")
