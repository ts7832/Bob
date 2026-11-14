"""Work folders for a room-layer run on plans the prompt was NOT tuned on (run from the repo root):

    python prep_heldout.py <outdir> <plan> [<plan> ...]

Per plan: <outdir>/<plan>/start_model.json = detector symbols and floors (stored predictions) + the wall model's room
outlines as unnamed candidates, and <outdir>/<plan>/ocr_text.json = OCR words with conf >= 0.5. Same format as the
round-3 work folders built from fix2/ + cand/ + ocr/.
"""
import json, os, resource, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.getcwd())
import cv2
from prep_fix import ROOMS, sheet_to_model
from drawqa import ocr, solve as S

out, plans = sys.argv[1], sys.argv[2:]
for plan in plans:
    img_path = f"junction-dataset-preview/data/cubicasa5k/{plan}.png"
    start = sheet_to_model(S.load_sheet(img_path, False))
    rooms = []
    for r in S.load_sheet(img_path, True).get("rooms", []):
        poly = r.get("polygon")
        if poly and len(poly) >= 3:
            rooms.append(dict(id=f"c{len(rooms) + 1}", name=None, type=r.get("cls") if r.get("cls") in ROOMS else None,
                              polygon=[[round(x, 1), round(y, 1)] for x, y in poly], source="wall model"))
    words = [dict(text=x["text"], bbox=x["bbox"]) for x in ocr.texts(cv2.imread(img_path)) if x["conf"] >= 0.5]
    os.makedirs(f"{out}/{plan}", exist_ok=True)
    json.dump(dict(start, rooms=rooms), open(f"{out}/{plan}/start_model.json", "w"), indent=1)
    json.dump(words, open(f"{out}/{plan}/ocr_text.json", "w"), indent=0)
    print(plan[-12:], "outlines", len(rooms), "doors", len(start["doors"]), "windows", len(start["windows"]),
          "words", len(words), "| peak MB", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 2**20, flush=True)
