"""Run the pretrained CubiCasa5k multi-task model on floor-plan PNGs and dump instance counts.

usage: python run_cubicasa.py <repo_dir> <weights.pkl> <out.jsonl> <img> [<img> ...]
"""
import sys, json, time, collections, signal, os
import numpy as np, cv2, torch
import torch.nn.functional as F

repo, weights, out_path, imgs = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
sys.path.insert(0, repo)
from floortrans.models import get_model
from floortrans.post_prosessing import split_prediction, get_polygons

ROOM = ["Background", "Outdoor", "Wall", "Kitchen", "Living Room", "Bed Room", "Bath", "Entry", "Railing", "Storage", "Garage", "Undefined"]
ICON = ["No Icon", "Window", "Door", "Closet", "Electrical Applience", "Toilet", "Sink", "Sauna Bench", "Fire Place", "Bathtub", "Chimney"]
N_CLASSES, SPLIT, MAX_SIDE = 44, [21, 12, 11], 1600

dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
model = get_model("hg_furukawa_original", 51)
model.conv4_ = torch.nn.Conv2d(256, N_CLASSES, bias=True, kernel_size=1)
model.upsample = torch.nn.ConvTranspose2d(N_CLASSES, N_CLASSES, kernel_size=4, stride=4)
ck = torch.load(weights, map_location="cpu", weights_only=True)
model.load_state_dict(ck["model_state"])
model.eval().to(dev)


def rot_img(t, k):  # rotate NCHW tensor by k*90 deg
    return torch.rot90(t, k, dims=(2, 3))


def predict(img):
    h, w = img.shape[:2]
    s = min(1.0, MAX_SIDE / max(h, w))
    if s < 1:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    x = torch.from_numpy(2 * (img / 255.0) - 1).float().permute(2, 0, 1)[None].to(dev)
    acc = torch.zeros(1, N_CLASSES, h, w)
    with torch.no_grad():
        # rotation TTA for the segmentation channels only (heatmaps are orientation-specific)
        for k in range(4):
            p = model(rot_img(x, k))
            p = rot_img(p, -k)
            p = F.interpolate(p, size=(h, w), mode="bilinear", align_corners=True).cpu()
            if k == 0:
                acc[:, :21] = p[:, :21]
            acc[:, 21:] += p[:, 21:] / 4
    return acc, (h, w), s


def _timeout(*_): raise TimeoutError("postproc timeout")
signal.signal(signal.SIGALRM, _timeout)
done = set()
if os.path.exists(out_path):
    done = {json.loads(l)["path"] for l in open(out_path)}

with open(out_path, "a") as out:
    for path in imgs:
        if path in done: continue
        t0 = time.time(); status = "ok"
        img = cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)
        pred, size, scale = predict(img)
        heat, rooms, icons = split_prediction(pred, size, SPLIT)
        try:
            signal.alarm(30)
            polys, types, room_polys, room_types = get_polygons((heat, rooms, icons), 0.2, [1, 2])
        except Exception as e:  # post-processing can fail or hang on odd plans
            status = type(e).__name__
            print("postproc failed", path, e, flush=True)
            polys, types, room_polys, room_types = [], [], [], []
        finally:
            signal.alarm(0)
        icon_c = collections.Counter(ICON[t["class"]] for t in types if t["type"] == "icon")
        open_c = collections.Counter(ICON[t["class"]] for t in types if t["type"] not in ("icon", "wall"))
        room_c = collections.Counter()
        for p, t in zip(room_polys, room_types):  # merge_rectangles merges per class -> count parts
            parts = getattr(p, "geoms", [p])
            room_c[ROOM[t["class"]]] += sum(1 for g in parts if not g.is_empty and g.area > 400)
        # pixel-level fallback counts: connected components of the icon segmentation
        seg = np.argmax(icons, axis=0)
        cc = {}
        for k in (1, 2, 5, 6, 7, 9):
            n, _, stats, _ = cv2.connectedComponentsWithStats((seg == k).astype(np.uint8))
            cc[ICON[k]] = int(sum(stats[1:, cv2.CC_STAT_AREA] >= 30))
        rseg = np.argmax(rooms, axis=0); room_cc = {}
        for k in (1, 3, 5, 6):
            n, _, st, _ = cv2.connectedComponentsWithStats((rseg == k).astype(np.uint8))
            room_cc[ROOM[k]] = int(sum(st[1:, cv2.CC_STAT_AREA] >= 400))
        rec = dict(path=path, status=status, room_cc=room_cc, scale=scale, secs=round(time.time() - t0, 1), icons=icon_c, openings=open_c,
                   rooms=room_c, cc=cc,
                   inst=[dict(type=t["type"], cls=ICON[t["class"]], poly=np.asarray(p).round(1).tolist())
                         for p, t in zip(polys, types) if t["type"] != "wall"],
                   room_inst=[dict(cls=ROOM[t["class"]], area_px=round(float(p.area), 1),
                                   pt=[round(c, 1) for c in p.representative_point().coords[0]])
                              for p, t in zip(room_polys, room_types) if not p.is_empty])
        out.write(json.dumps(rec) + "\n"); out.flush()
        print(f"{rec['secs']:5.1f}s {path.split('/')[-1]} icons={dict(icon_c)} open={dict(open_c)} rooms={dict(room_c)}", flush=True)
