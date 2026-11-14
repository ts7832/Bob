"""Score a room layer against CubiCasa's own room polygons (the annotation behind the dataset).

    python room_eval.py <layer_dir> [<layer_dir> ...]     each layer_dir holds <plan>/model_final.json (or start_model.json)

Per layer: rooms found (GT rooms matched with IoU >= 0.5), extra rooms (model rooms matching no GT room at IoU >= 0.3),
mean IoU per GT room (0 if unmatched), median area error of matched rooms, and type accuracy on matched rooms whose GT
type is known. GT spaces: every 'Space ...' polygon except plain 'Outdoor'. Spaces with no known type (Undefined, a yard) are
optional: they stay in the matching, so a model room on them is not extra, but missing them costs nothing.
"""
import json, os, re, statistics, sys
from xml.dom import minidom
from PIL import Image
from shapely.geometry import Polygon
from shapely.validation import make_valid

P = os.path.dirname(os.path.abspath(__file__))
DS = "/Users/alexp/Documents/junction/junction-dataset-preview/data/cubicasa5k"
OTHER = [("LivingRoom", "living_room"), ("Dining", "dining"), ("Entry", "entry"), ("DraughtLobby", "entry"), ("Hall", "entry"),
         ("Closet", "storage"), ("Storage", "storage"), ("Utility", "utility"), ("TechnicalRoom", "utility"), ("DressingRoom", "dressing_room"),
         ("Outdoor Terrace", "terrace"), ("Outdoor Porch", "terrace"), ("Outdoor Patio", "terrace"), ("Outdoor Veranda", "terrace"),
         ("Outdoor CoveredArea", "terrace"), ("Outdoor Pergola", "terrace"), ("Garage", "garage"), ("CarPort", "garage"),
         ("Room", "room"), ("Den", "room"), ("RecreationRoom", "room"), ("Office", "room"), ("Library", "room"), ("Alcove", "room"),
         ("UserDefined", "other_room")]
SAME = {"dressing_room": "storage"}  # a walk-in closet (VH) is annotated either way


def _text(g):
    for t in g.getElementsByTagName("text"):
        s = "".join(n.data for n in t.childNodes if n.nodeType == n.TEXT_NODE).strip()
        if s:
            return s.upper()
    return ""


def gt_rooms(plan):
    d = f"{P}/gt/{plan}"
    w, h = Image.open(f"{DS}/{plan}.png").size
    ws, hs = Image.open(f"{d}/F1_scaled.png").size
    sx, sy = w / ws, h / hs
    out = []
    for g in minidom.parse(f"{d}/model.svg").getElementsByTagName("g"):
        cls = g.getAttribute("class")
        if not cls.startswith("Space "):
            continue
        kind, lab = cls[6:], _text(g)
        if kind.strip() == "Outdoor":
            continue
        pol = next((p for p in g.childNodes if p.nodeName == "polygon"), None)
        if pol is None:
            continue
        pts = [(float(a.split(",")[0]) * sx, float(a.split(",")[1]) * sy) for a in pol.getAttribute("points").split() if "," in a]
        if len(pts) < 3:
            continue
        t = ("bedroom" if kind.startswith("Bedroom") else "kitchen" if kind.startswith("Kitchen")
             else ("wc_room" if re.fullmatch(r"(INV\.?)? ?WC.*", lab) else "bathroom") if kind.startswith("Bath")
             else "balcony" if kind.startswith("Outdoor Balcony") else "sauna_room" if kind.startswith("Sauna") or lab in ("S", "SAUNA")
             else next((c for p, c in OTHER if kind.startswith(p)), None))
        out.append(dict(type=t, label=lab, poly=pts))
    return out


def poly(pts):
    g = make_valid(Polygon(pts))
    return g if g.area > 0 else None


def score(plan, rooms):
    gt = [(r, poly(r["poly"])) for r in gt_rooms(plan)]
    gt = [(r, g) for r, g in gt if g is not None]
    md = [(r, poly(r["polygon"])) for r in rooms if len(r.get("polygon") or []) >= 3]
    md = [(r, g) for r, g in md if g is not None]
    pairs = []
    for i, (_, a) in enumerate(gt):
        for j, (_, b) in enumerate(md):
            inter = a.intersection(b).area
            if inter > 0:
                pairs.append((inter / a.union(b).area, i, j))
    pairs.sort(reverse=True)
    gi, mj, match = set(), set(), {}
    for iou, i, j in pairs:
        if i in gi or j in mj or iou < 0.3:
            continue
        gi.add(i); mj.add(j); match[i] = (j, iou)
    req = [i for i in range(len(gt)) if gt[i][0]["type"] is not None]  # unknown-type spaces (yard, undefined) are optional
    ious = [match[i][1] if i in match else 0.0 for i in req]
    found = sum(1 for i in req if i in match and match[i][1] >= 0.5)
    extra = len(md) - len(mj)
    aerr = [abs(md[j][1].area / gt[i][1].area - 1) for i, (j, iou) in match.items()]
    typed = [(gt[i][0]["type"], md[j][0].get("type")) for i, (j, iou) in match.items() if gt[i][0]["type"] not in (None, "other_room")]
    tok = sum(1 for a, b in typed if b is not None and (a == b or SAME.get(b) == a or SAME.get(a) == b))
    return dict(n_gt=len(req), n_model=len(md), found=found, extra=extra, iou_sum=sum(ious), aerr=aerr, typed=len(typed), type_ok=tok)


def evaluate(layer_dir, plans=None, quiet=False):
    tot = dict(n_gt=0, n_model=0, found=0, extra=0, iou_sum=0.0, aerr=[], typed=0, type_ok=0)
    for plan in sorted(plans or os.listdir(layer_dir)):
        f = next((x for x in (f"{layer_dir}/{plan}/model_final.json", f"{layer_dir}/{plan}/start_model.json") if os.path.exists(x)), None)
        if not f or not os.path.isdir(f"{P}/gt/{plan}"):
            continue
        s = score(plan, json.load(open(f)).get("rooms", []))
        for k in tot:
            tot[k] += s[k]
        if not quiet:
            print(f"   {plan[-12:]:12s} gt {s['n_gt']:2d} model {s['n_model']:2d} found {s['found']:2d} extra {s['extra']:2d} meanIoU {s['iou_sum'] / max(1, s['n_gt']):.2f} types {s['type_ok']}/{s['typed']}")
    n = max(1, tot["n_gt"])
    return dict(found=f"{tot['found']}/{tot['n_gt']}", extra=tot["extra"], mean_iou=round(tot["iou_sum"] / n, 3),
                median_area_err=round(statistics.median(tot["aerr"]) * 100, 1) if tot["aerr"] else None,
                types=f"{tot['type_ok']}/{tot['typed']}")


if __name__ == "__main__":
    for d in [a for a in sys.argv[1:] if not a.startswith("-")]:
        print(os.path.basename(d.rstrip("/")), evaluate(d, quiet="-q" in sys.argv), flush=True)
