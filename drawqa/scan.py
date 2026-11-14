"""Scanned plans (cubicasa5k): sheet graph from detector predictions.

Prediction files in data/predictions/ (newest yolo_*.jsonl wins, override with DRAWQA_PRED=<file>):
  yolo format     : {"path": "<name>.png", "model": "yolo:v1", "boxes": [{"cls": "door", "bbox": [x0,y0,x1,y1], "conf": 0.9}]}
  baseline format : output of tools/run_cubicasa.py (pretrained CubiCasa5k model)
"""
import glob, json, os
from PIL import Image

PRED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "predictions")
BASELINE_MAP = {"Window": "window", "Door": "door", "Toilet": "wc", "Sink": "sink", "Bathtub": "bathtub", "Sauna Bench": "sauna_bench"}
OPENINGS = {"door", "window"}
ROOMS = {"bedroom", "kitchen", "bathroom", "wc_room", "balcony", "sauna_room",
         "room", "living_room", "dining", "entry", "storage", "utility", "dressing_room", "terrace", "garage", "other_room"}
CONF_MIN = float(os.environ.get("DRAWQA_CONF", 0.35))

_preds = None


def pred_file():
    if os.environ.get("DRAWQA_PRED"):
        return os.environ["DRAWQA_PRED"]
    active = os.path.join(PRED_DIR, "active.txt")  # one line: the predictions file to use by default
    if os.path.exists(active):
        return os.path.join(PRED_DIR, open(active).read().strip())
    yolo = sorted(glob.glob(os.path.join(PRED_DIR, "yolo_*.jsonl")), key=os.path.getmtime)
    yolo = [f for f in yolo if "smoke" not in f]
    return yolo[-1] if yolo else os.path.join(PRED_DIR, "cubicasa_baseline.jsonl")


def predictions():
    global _preds
    if _preds is None:
        _preds, f = {}, pred_file()
        if os.path.exists(f):
            for line in open(f):
                r = json.loads(line)
                _preds[os.path.basename(r["path"])] = r
    return _preds


_tuned = None


def tuned():
    """Thresholds and merge rules tuned for exact counts (written next to the predictions file by the tune stage)."""
    global _tuned
    if _tuned is None:
        f = os.environ.get("DRAWQA_THR") or os.path.splitext(pred_file())[0] + ".thresholds.json"
        _tuned = json.load(open(f)) if os.path.exists(f) and "DRAWQA_CONF" not in os.environ else {}
    return _tuned


def thresholds():
    return tuned().get("thresholds", {})


def _boxes(r):
    """Normalise both prediction formats to [(cls, bbox, conf)]."""
    if "boxes" in r:
        from .postprocess import apply
        return [(d["cls"], d["bbox"], d["conf"]) for d in apply([dict(cls=b["cls"], bbox=b["bbox"], conf=b.get("conf", 1.0)) for b in r["boxes"]],
                                                                 thresholds(), tuned().get("rules"), CONF_MIN)]
    s = r.get("scale", 1.0) or 1.0
    out = []
    for inst in r.get("inst", []):
        if inst["cls"] in BASELINE_MAP:
            xs, ys = [p[0] / s for p in inst["poly"]], [p[1] / s for p in inst["poly"]]
            out.append((BASELINE_MAP[inst["cls"]], [min(xs), min(ys), max(xs), max(ys)], 0.5))
    return out


def load(path):
    w, h = Image.open(path).size
    sheet = dict(source=path, source_type="raster", units="px", y_flip=False, rooms=[], openings=[], fixtures=[],
                 render=dict(type="image", width=w, height=h))
    r = predictions().get(os.path.basename(path))
    if r is None:
        sheet["note"] = "No detector predictions for this plan yet."
        sheet["model"] = "none"
        return sheet
    sheet["model"] = r.get("model", "CubiCasa5k pretrained (2019), not fine-tuned")
    sheet["floors"] = []
    for i, (cls, bb, conf) in enumerate(_boxes(r)):
        group = "openings" if cls in OPENINGS else "rooms" if cls in ROOMS else "floors" if cls == "floor" else "fixtures"
        sheet[group].append(dict(id=f"{cls}{i}", cls=cls, bbox=bb, conf=conf))
    sheet["detector_classes"] = sorted(thresholds()) or None  # what this detector was trained to find
    if "boxes" in r:
        from .postprocess import detector_fit
        sheet["detector_fit"] = detector_fit(r["boxes"], thresholds(), CONF_MIN)
    sheet["room_classes_available"] = any(b.get("cls") in ROOMS or b.get("cls") == "floor" for b in r.get("boxes", []))
    return sheet
