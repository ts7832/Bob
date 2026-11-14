"""Prepare the 'detector first, frontier model fixes' test (run from the repo root):

    python prep_fix.py <outdir> <plan name> [<plan name> ...]

For each plan: the sheet our engine normally answers from (solve.load_sheet: stored detector predictions + wall model +
printed names) -> start_model.json in plan_tool.py's format; the dataset questions -> <plan>.KEY.json; our engine's own
answers -> <plan>.ENGINE.json; and a round-trip check: our engine on start_model.json must score like the engine itself.
"""
import json, os, sys

sys.path.insert(0, os.getcwd())
from drawqa import solve as S

DS = "junction-dataset-preview/"
ROOMS = {"bedroom", "kitchen", "bathroom", "wc_room", "balcony", "sauna_room", "room", "living_room", "dining", "entry",
         "storage", "utility", "dressing_room", "terrace", "garage", "other_room"}


def box_poly(b):
    return [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]


def sheet_to_model(sheet):
    r1 = lambda v: [round(float(x), 1) for x in v]
    m = dict(m_per_px=None, scale_source="", floors=[], rooms=[], doors=[], windows=[], fixtures=[])
    for i, f in enumerate(sheet.get("floors", [])):
        m["floors"].append(dict(id=f"F{i + 1}", bbox=r1(f["bbox"])))
    for o in sheet.get("openings", []):
        (m["doors"] if o["cls"] == "door" else m["windows"]).append(dict(id=f"{o['cls'][0]}{len(m['doors' if o['cls'] == 'door' else 'windows']) + 1}", bbox=r1(o["bbox"])))
    for i, f in enumerate(sheet.get("fixtures", [])):
        m["fixtures"].append(dict(id=f"f{i + 1}", type=f["cls"], bbox=r1(f["bbox"])))
    for i, r in enumerate(sheet.get("rooms", [])):
        if r.get("cls") not in ROOMS:
            continue
        poly = r.get("polygon") or box_poly(r["bbox"])
        name = r.get("name") or (r.get("names") or [None])[0] or r.get("label")
        m["rooms"].append(dict(id=f"r{i + 1}", name=name if isinstance(name, str) else None, type=r["cls"], polygon=[r1(p) for p in poly]))
    return m


def main():
    out, plans = sys.argv[1], sys.argv[2:]
    Q = [json.loads(l) for l in open(DS + "questions.jsonl")]
    for name in plans:
        d = os.path.join(out, name)
        os.makedirs(d, exist_ok=True)
        path = f"{DS}data/cubicasa5k/{name}.png"
        qs, seen = [], set()
        for q in Q:
            if q["path"].endswith("/" + name + ".png") and q["question"].startswith(("How many", "What is")) and q["question"] not in seen:
                seen.add(q["question"])
                qs.append(dict(question=q["question"], answer=q["answer"], type=q["type"]))
        json.dump(qs, open(f"{d}/KEY.json", "w"), indent=1)
        eng = [dict(question=q["question"], ours=S.solve(path, q["question"]).get("answer"), key=q["answer"]) for q in qs]
        json.dump(eng, open(f"{d}/ENGINE.json", "w"), indent=1)
        sheet = S.load_sheet(path)
        json.dump(sheet_to_model(sheet), open(f"{d}/start_model.json", "w"), indent=1)
        print(name, len(qs), "questions; engine", sum(e["ours"] == e["key"] for e in eng), "right", flush=True)


if __name__ == "__main__":
    main()
