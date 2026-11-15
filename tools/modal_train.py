"""Train the element detector for scanned floor plans on Modal.

One detector marks symbols (door, window, toilet, sink, bathtub, shower, sauna stove/bench) and regions
(bedroom, kitchen, bathroom, WC room, balcony, sauna room, floor). Labels come from CubiCasa5k's own SVG
annotations, following the counting rules of the Junction answer key (checked on 131 plans, 1,208/1,210):
  - door = Door element with a leaf (class 'Door None ...' = open doorway, not labelled)
  - shower = Shower + ShowerPlatform (NOT ShowerCab / ShowerScreen); bathtub = Bathtub*; sink = any *Sink*
  - bathroom = Space Bath* whose label is not WC; wc_room = Space Bath* labelled WC
  - floor = one box per Floorplan group
Images are CubiCasa's F1_original.png, which is pixel-identical to the Junction PNG; SVG coordinates are in
F1_scaled.png space and are scaled down. The 241 preview plans are never used for training or tuning.

Stages (run from repo root; --detach keeps a long run alive if the laptop disconnects):
  .venv/bin/modal run tools/modal_train.py --stage download
  .venv/bin/modal run tools/modal_train.py --stage convert
  .venv/bin/modal run --detach tools/modal_train.py --stage train --run v1 --model yolo11m.pt --epochs 60
  .venv/bin/modal run tools/modal_train.py --stage tune --run v1       # per-class thresholds for exact counts
  .venv/bin/modal run tools/modal_train.py --stage predict --run v1    # -> data/predictions/yolo_v1.jsonl
  .venv/bin/modal run tools/modal_train.py --stage smoke               # tiny end-to-end test
GPU type: DRAWQA_GPU (default A100-40GB).
"""
import glob, json, os, pathlib

import modal

APP = modal.App("drawqa-train")
VOL = modal.Volume.from_name("drawqa-data", create_if_missing=True)
ROOT = pathlib.Path(__file__).resolve().parent.parent
D = "/data"
GPU = os.environ.get("DRAWQA_GPU", "A100-40GB")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0", "unzip", "curl")
    .pip_install("ultralytics==8.4.212", "scikit-image", "svgpathtools", "opencv-python-headless", "numpy<2", "pymupdf")
    # training settings fixed when the image is built (the container does not see the local shell's variables)
    .env({"DRAWQA_SCALE": os.environ.get("DRAWQA_SCALE", "0.3"), "DRAWQA_SAVE_PERIOD": os.environ.get("DRAWQA_SAVE_PERIOD", "-1")})
    .add_local_dir(str(ROOT / "ext" / "cubicasa" / "floortrans" / "loaders"), "/root/cubi_loaders")
    .add_local_dir(str(ROOT / "drawqa"), "/root/drawqa", ignore=["__pycache__"])
    .add_local_file(str(ROOT / "tools" / "swiss_labels.py"), "/root/swiss_labels.py")
)

CLASSES = ["door", "window", "wc", "sink", "bathtub", "shower", "sauna_stove", "sauna_bench",
           "bedroom", "kitchen", "bathroom", "wc_room", "balcony", "sauna_room", "floor",
           # every other labelled room, so an uploaded plan gets all of its rooms (the dataset asks no questions about these)
           "room", "living_room", "dining", "entry", "storage", "utility", "dressing_room", "terrace", "garage", "other_room"]
# CubiCasa 'Space <kind>' (matched by prefix, in this order) -> class. Counted over all 5,000 plans with --stage space_stats.
# Rooms the answer key counts (bedroom, kitchen, bath, balcony, sauna) keep their own rules in _parse_one and come first.
OTHER_ROOMS = [("LivingRoom", "living_room"), ("Dining", "dining"),
               ("Entry", "entry"), ("DraughtLobby", "entry"), ("Hall", "entry"),
               ("Closet", "storage"), ("Storage", "storage"),
               ("Utility", "utility"), ("TechnicalRoom", "utility"), ("DressingRoom", "dressing_room"),
               ("Outdoor Terrace", "terrace"), ("Outdoor Porch", "terrace"), ("Outdoor Patio", "terrace"),
               ("Outdoor Veranda", "terrace"), ("Outdoor CoveredArea", "terrace"), ("Outdoor Pergola", "terrace"),
               ("Garage", "garage"), ("CarPort", "garage"),
               ("Room", "room"), ("Den", "room"), ("RecreationRoom", "room"), ("Office", "room"), ("Library", "room"), ("Alcove", "room"),
               ("UserDefined", "other_room")]  # a room with a combined or unusual label (e.g. 'KHH+PH', 'OH+RT+K')
# Not labelled: 'Undefined' (spaces with no printed name), plain 'Outdoor' (the plot around the building), attics, lifts.


def style_variant(img, seed):
    """The same drawing as it might arrive from elsewhere: photocopy, fax, heavy or thin lines, a low-resolution
    JPEG, a blueprint, aged paper. Geometry is untouched, so the labels of the original apply."""
    import cv2, numpy as np
    rng = np.random.default_rng(seed)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    bgr = lambda a: cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    k = seed % 7
    if k == 3 and max(h, w) < 1200:
        k = 0  # thinning erases the lines of a small scan
    if k == 0:
        return bgr(g)
    if k == 1:
        return bgr(cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
    if k == 2:
        n = 3 if max(h, w) >= 1500 else 2
        return bgr(cv2.erode(g, np.ones((n, n), np.uint8)))  # dark lines grow
    if k == 3:
        return bgr(cv2.dilate(g, np.ones((2, 2), np.uint8)))  # dark lines shrink
    if k == 4:
        f = float(rng.uniform(0.5, 0.75))
        small = cv2.resize(img, (max(8, int(w * f)), max(8, int(h * f))), interpolation=cv2.INTER_AREA)
        back = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        return cv2.imdecode(cv2.imencode(".jpg", back, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(18, 40))])[1], cv2.IMREAD_COLOR)
    if k == 5:
        ink = (255 - g).astype(np.float32) / 255  # white lines on blue
        return np.stack([120 + ink * 135, 45 + ink * 210, 15 + ink * 240], -1).clip(0, 255).astype(np.uint8)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    light = 0.86 + 0.14 * (xx / w * rng.uniform(-1, 1) + yy / h * rng.uniform(-1, 1))
    paper = g.astype(np.float32) * light + rng.normal(0, 7, g.shape)
    tint = np.array([0.80, 0.93, 1.0], np.float32)  # BGR: yellowed paper
    return (paper[..., None] * tint).clip(0, 255).astype(np.uint8)


def _seed(name):
    import zlib
    return zlib.crc32(name.encode())
FIXTURES = {
    "Toilet": "wc",
    "Sink": "sink", "SideSink": "sink", "RoundSink": "sink", "CornerSink": "sink", "DoubleSink": "sink", "DoubleSinkRight": "sink",
    "Bathtub": "bathtub", "BathtubRound": "bathtub",
    "Shower": "shower", "ShowerPlatform": "shower",
    "SaunaBench": "sauna_bench", "SaunaBenchHigh": "sauna_bench", "SaunaBenchLow": "sauna_bench", "SaunaBenchMid": "sauna_bench",
    "SaunaStove": "sauna_stove",
}
ZIP_URL = "https://zenodo.org/records/2613548/files/cubicasa5k.zip?download=1"
JUNCTION_ZIP = "https://fwjydgxpttajiqpfmqtj.supabase.co/storage/v1/object/public/stoa-junction-2026/junction-dataset.zip"
TILE = 1280  # sheets larger than this are cut into overlapping tiles so symbols keep their native size


def count_keys(n):
    """Detector class counts -> the 12 question types of the answer key."""
    return dict(windows=n.get("window", 0), doors=n.get("door", 0), wc=n.get("wc", 0), sinks=n.get("sink", 0),
                shower_bath=n.get("shower", 0) + n.get("bathtub", 0), floors=n.get("floor", 0),
                bedrooms=n.get("bedroom", 0), kitchens=n.get("kitchen", 0), bathrooms=n.get("bathroom", 0),
                wc_rooms=n.get("wc_room", 0), balconies=n.get("balcony", 0),
                saunas=max(n.get("sauna_stove", 0), min(1, n.get("sauna_bench", 0)), n.get("sauna_room", 0)))


def preview_ids():
    """Preview file 'high_quality_architectural-13040.png' == CubiCasa folder '/high_quality_architectural/13040/'."""
    names = [os.path.basename(p)[:-4] for p in glob.glob(str(ROOT / "junction-dataset-preview/data/cubicasa5k/*.png"))]
    return sorted("/" + n.rsplit("-", 1)[0] + "/" + n.rsplit("-", 1)[1] + "/" for n in names)


dl_image = modal.Image.debian_slim(python_version="3.11").apt_install("aria2", "unzip")


@APP.function(image=dl_image, volumes={D: VOL}, timeout=4 * 3600, cpu=4)
def download():
    """CubiCasa5k zip from Zenodo (5.2 GB). One connection gets ~1 MB/s, so fetch 8 ranges in parallel."""
    import subprocess
    if os.path.exists(f"{D}/cubicasa5k/train.txt"):
        return "already downloaded"
    for f in glob.glob(f"{D}/c5k.zip*"):
        os.remove(f)
    subprocess.run(["aria2c", "-x", "8", "-s", "8", "-k", "50M", "--retry-wait=10", "--max-tries=20", "--summary-interval=30",
                    "--console-log-level=warn", "-d", "/tmp", "-o", "c5k.zip", ZIP_URL], check=True)
    subprocess.run(["unzip", "-q", "-o", "/tmp/c5k.zip", "-d", D], check=True)
    VOL.commit()
    return subprocess.run(["bash", "-c", f"ls {D}/cubicasa5k | head; wc -l {D}/cubicasa5k/*.txt; du -sh {D}/cubicasa5k"], capture_output=True, text=True).stdout


def _text(g):
    for t in g.getElementsByTagName("text"):
        s = "".join(n.data for n in t.childNodes if n.nodeType == n.TEXT_NODE).strip()
        if s:
            return s.upper()
    return ""


def _poly_pts(g):
    """Points of the first <polygon> child of a group, in SVG coordinates."""
    pol = next((p for p in g.childNodes if p.nodeName == "polygon"), None)
    if pol is None:
        return [], []
    pts = [a.split(",") for a in pol.getAttribute("points").split() if "," in a]
    return [float(x) for x, _ in pts], [float(y) for _, y in pts]


def _in_poly(px, py, X, Y):
    """Point in polygon (ray casting); X, Y = the polygon's corner coordinates."""
    inside, n = False, len(X)
    for i in range(n):
        x0, y0, x1, y1 = X[i], Y[i], X[(i + 1) % n], Y[(i + 1) % n]
        if (y0 > py) != (y1 > py) and px < x0 + (py - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _parse_one(args):
    """One plan -> image copy + YOLO label file. Returns (name, class counts, parse errors) or None."""
    import re, shutil, sys
    from xml.dom import minidom
    from PIL import Image
    sys.path.insert(0, "/root/cubi_loaders")
    import svg_utils  # CubiCasa's own SVG helpers, for fixture icons with nested transforms

    folder, split = args
    src = f"{D}/cubicasa5k{folder}"
    try:
        w, h = Image.open(src + "F1_original.png").size
        ws, hs = Image.open(src + "F1_scaled.png").size
        dom = minidom.parse(src + "model.svg")
    except Exception:
        return None
    sx, sy = w / ws, h / hs
    boxes, errors = [], 0

    def add(cls, X, Y, pad=2.0):
        if len(X) < 2:
            return
        x0, x1 = max(0.0, min(X) * sx - pad), min(float(w), max(X) * sx + pad)
        y0, y1 = max(0.0, min(Y) * sy - pad), min(float(h), max(Y) * sy + pad)
        if x1 - x0 >= 3 and y1 - y0 >= 3:
            boxes.append((CLASSES.index(cls), x0, y0, x1, y1))

    is_wc = lambda lab: re.fullmatch(r"(INV\.?)? ?WC.*", lab) is not None
    sauna_marks, undecided = [], []
    for g in dom.getElementsByTagName("g"):
        cls_attr, gid = g.getAttribute("class"), g.getAttribute("id")
        toks = cls_attr.split()
        try:
            if gid == "Door":
                if len(toks) > 1 and toks[1] == "None":
                    continue  # open doorway without a leaf: the question does not count these
                add("door", *_poly_pts(g))
            elif gid == "Window":
                add("window", *_poly_pts(g))
            elif cls_attr.startswith("FixedFurniture "):
                cls = FIXTURES.get(toks[-1])
                if cls is None:
                    continue
                _, _, X, Y = svg_utils.get_icon(g)
                add(cls, list(X), list(Y), pad=1.0)
                if cls in ("sauna_stove", "sauna_bench") and len(X):
                    sauna_marks.append(((min(X) + max(X)) / 2, (min(Y) + max(Y)) / 2))
            elif cls_attr.startswith("Space "):
                kind, lab = cls_attr[6:], _text(g)
                room = ("bedroom" if kind.startswith("Bedroom") else "kitchen" if kind.startswith("Kitchen")
                        else ("wc_room" if is_wc(lab) else "bathroom") if kind.startswith("Bath")
                        else "balcony" if kind.startswith("Outdoor Balcony")
                        else "sauna_room" if kind.startswith("Sauna") or lab in ("S", "SAUNA") else None)
                if room is None:
                    room = next((c for prefix, c in OTHER_ROOMS if kind.startswith(prefix)), None)
                if room in (None, "other_room"):
                    undecided.append((room, *_poly_pts(g)))  # may turn out to be a sauna, see below
                elif room:
                    add(room, *_poly_pts(g), pad=0.0)
            elif "Floorplan" in toks:
                X, Y = [], []
                for sub in g.getElementsByTagName("g"):
                    if sub.getAttribute("id") == "Wall" or sub.getAttribute("class").startswith("Space "):
                        x, y = _poly_pts(sub)
                        X += x
                        Y += y
                add("floor", X, Y, pad=4.0)
        except Exception:
            errors += 1
    # CubiCasa names only 133 rooms 'Sauna' in 5,000 plans, although 2,877 plans-worth of sauna stoves are drawn: most
    # saunas are annotated 'Undefined'. A space with no usable label that holds a sauna stove or bench IS the sauna.
    for room, X, Y in undecided:
        if len(X) >= 3 and any(_in_poly(mx, my, X, Y) for mx, my in sauna_marks):
            room = "sauna_room"
        if room:
            add(room, X, Y, pad=0.0)
    name = folder.strip("/").replace("/", "-")
    shutil.copy(src + "F1_original.png", f"{D}/yolo/images/{split}/{name}.png")
    lines = "".join(f"{c} {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}\n" for c, x0, y0, x1, y1 in boxes)
    open(f"{D}/yolo/labels/{split}/{name}.txt", "w").write(lines)
    if split == "train" and _seed(name) % 2 == 0:  # half of the training plans also in another drawing style
        import cv2
        cv2.imwrite(f"{D}/yolo/images/{split}/{name}_s.png", style_variant(cv2.imread(src + "F1_original.png"), _seed(name) // 2))
        open(f"{D}/yolo/labels/{split}/{name}_s.txt", "w").write(lines)
    n = {}
    for c, *_ in boxes:
        n[CLASSES[c]] = n.get(CLASSES[c], 0) + 1
    return name, split, n, errors


def write_lists():
    """Image lists + yaml per training set: 'c5k' = scanned apartment plans only, 'all' = every source converted so far."""
    for split in ("train", "val"):
        files = sorted(glob.glob(f"{D}/yolo/images/{split}/*.png"))
        c5k = [f for f in files if not os.path.basename(f).startswith(("swiss-", "ifc-"))]
        open(f"{D}/yolo/{split}_c5k.txt", "w").write("\n".join(c5k) + "\n")
        open(f"{D}/yolo/{split}_all.txt", "w").write("\n".join(files) + "\n")
    names = "names:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASSES))
    for tag in ("c5k", "all"):
        open(f"{D}/yolo/data_{tag}.yaml", "w").write(f"path: {D}/yolo\ntrain: train_{tag}.txt\nval: val_{tag}.txt\n{names}")
    return {t: sum(1 for _ in open(f"{D}/yolo/train_{t}.txt")) for t in ("c5k", "all")}


def write_mixed_lists(extra: str, tag: str, max_train: int, max_val: int = 400):
    """Training set 'all' plus another YOLO folder (e.g. /data/yolo_fpc: images/{train,val}/*.png + labels/, labelled
    with CLASSES' indices). The extra share is capped so the scans keep their weight; the sample is fixed (seeded)."""
    import random
    out = {}
    for split, cap in (("train", max_train), ("val", max_val)):
        base = open(f"{D}/yolo/{split}_all.txt").read().split()
        more = sorted(glob.glob(f"{extra}/images/{split}/*.png"))
        more = [f for f in more if os.path.exists(f.replace("/images/", "/labels/")[:-4] + ".txt")]
        if cap and len(more) > cap:
            more = sorted(random.Random(0).sample(more, cap))
        open(f"{D}/yolo/{split}_{tag}.txt", "w").write("\n".join(base + more) + "\n")
        out[split] = dict(base=len(base), extra=len(more))
    names = "names:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASSES))
    open(f"{D}/yolo/data_{tag}.yaml", "w").write(f"path: {D}/yolo\ntrain: train_{tag}.txt\nval: val_{tag}.txt\n{names}")
    return out


@APP.function(image=image, volumes={D: VOL}, timeout=900)
def mix_lists(extra: str, tag: str, max_train: int):
    VOL.reload()
    out = write_mixed_lists(extra, tag, max_train)
    VOL.commit()
    return out


@APP.function(image=image, volumes={D: VOL}, timeout=2 * 3600, cpu=8, memory=16384)
def convert(exclude: list, limit: int = 0):
    import shutil, collections
    from multiprocessing import Pool
    VOL.reload()
    shutil.rmtree(f"{D}/yolo", ignore_errors=True)
    for s in ("train", "val"):
        os.makedirs(f"{D}/yolo/images/{s}", exist_ok=True)
        os.makedirs(f"{D}/yolo/labels/{s}", exist_ok=True)
    ex = set(exclude)
    read = lambda f: [l.strip() for l in open(f"{D}/cubicasa5k/{f}.txt") if l.strip()]
    splits = {"train": read("train") + read("test"), "val": read("val")}  # CubiCasa's test split joins training
    jobs, info = [], {}
    for split, folders in splits.items():
        kept = [f for f in folders if f not in ex]
        info[split] = dict(plans=len(folders), excluded_preview=len(folders) - len(kept))
        jobs += [(f, split) for f in (kept[:limit] if limit else kept)]
    with Pool(8) as p:
        res = [r for r in p.map(_parse_one, jobs, chunksize=8) if r]
    counts = collections.Counter()
    gt = {}
    for name, split, n, errors in res:
        counts.update(n)
        gt[name] = dict(split=split, classes=n, keys=count_keys(n), errors=errors)
    json.dump(gt, open(f"{D}/yolo/gt_counts.json", "w"))
    with open(f"{D}/yolo/data.yaml", "w") as f:
        f.write(f"path: {D}/yolo\ntrain: images/train\nval: images/val\nnames:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASSES)))
    lists = write_lists()
    VOL.commit()
    return dict(splits=info, converted=len(res), failed=len(jobs) - len(res), parse_errors=sum(r[3] for r in res), boxes=dict(counts), train_images=lists)


# ---- walls and room outlines (a second, segmentation model on the same scanned plans) --------------------------------
# The detector gives boxes. Areas and "which wall is this window in" need outlines, so a segmentation model learns
# CubiCasa's own wall polygons and room polygons. 'outdoor' = balconies, terraces etc. (kept apart so indoor area can
# be summed); the plot around the building (plain 'Space Outdoor') is not labelled.
SEG_CLASSES = ["wall", "room", "outdoor"]


def _seg_one(args):
    """One plan -> a YOLO segmentation label file for the image the detector set already holds (and its style variant)."""
    from xml.dom import minidom
    from PIL import Image
    folder, split = args
    src = f"{D}/cubicasa5k{folder}"
    name = folder.strip("/").replace("/", "-")
    if not os.path.exists(f"{D}/yolo/images/{split}/{name}.png"):
        return None
    try:
        w, h = Image.open(src + "F1_original.png").size
        ws, hs = Image.open(src + "F1_scaled.png").size
        dom = minidom.parse(src + "model.svg")
    except Exception:
        return None
    sx, sy = w / ws, h / hs
    lines, n = [], {}
    for g in dom.getElementsByTagName("g"):
        cls_attr, gid = g.getAttribute("class"), g.getAttribute("id")
        if gid == "Wall":
            cls = "wall"
        elif cls_attr.startswith("Space "):
            kind = cls_attr[6:].split()
            if kind == ["Outdoor"]:
                continue
            cls = "outdoor" if kind and kind[0] == "Outdoor" else "room"
        else:
            continue
        X, Y = _poly_pts(g)
        if len(X) < 3:
            continue
        pts = " ".join(f"{min(1.0, max(0.0, x * sx / w)):.6f} {min(1.0, max(0.0, y * sy / h)):.6f}" for x, y in zip(X, Y))
        lines.append(f"{SEG_CLASSES.index(cls)} {pts}\n")
        n[cls] = n.get(cls, 0) + 1
    names = [name] + ([name + "_s"] if os.path.exists(f"{D}/yolo/images/{split}/{name}_s.png") else [])
    for nm in names:
        open(f"{D}/yoloseg/labels/{split}/{nm}.txt", "w").write("".join(lines))
    return name, split, n, len(names)


@APP.function(image=image, volumes={D: VOL}, timeout=2 * 3600, cpu=8, memory=16384)
def convert_seg(exclude: list, limit: int = 0):
    """Wall and room-outline labels for every scanned plan of the detector set (same splits, same excluded preview plans)."""
    import shutil, collections
    from multiprocessing import Pool
    VOL.reload()
    shutil.rmtree(f"{D}/yoloseg", ignore_errors=True)
    for s in ("train", "val"):
        os.makedirs(f"{D}/yoloseg/labels/{s}", exist_ok=True)
    ex = set(exclude)
    read = lambda f: [l.strip() for l in open(f"{D}/cubicasa5k/{f}.txt") if l.strip()]
    splits = {"train": read("train") + read("test"), "val": read("val")}
    jobs = []
    for split, folders in splits.items():
        kept = [f for f in folders if f not in ex]
        jobs += [(f, split) for f in (kept[:limit] if limit else kept)]
    with Pool(8) as p:
        res = [r for r in p.map(_seg_one, jobs, chunksize=8) if r]
    counts, files = collections.Counter(), collections.Counter()
    for name, split, n, k in res:
        counts.update(n)
        files[split] += k
    VOL.commit()
    return dict(plans=len(res), skipped=len(jobs) - len(res), label_files=dict(files), polygons=dict(counts))


def _seg_local(splits=("train", "val")):
    """Copy the segmentation set to the container's local disk (labels from yoloseg, images from the detector set)."""
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    L = "/tmp/yoloseg"
    pairs = []
    for split in splits:
        os.makedirs(f"{L}/images/{split}", exist_ok=True)
        os.makedirs(f"{L}/labels/{split}", exist_ok=True)
        for lab in glob.glob(f"{D}/yoloseg/labels/{split}/*.txt"):
            nm = os.path.basename(lab)[:-4]
            own = f"{D}/yoloseg/images/{split}/{nm}.png"  # kept copy (stage seg_keep_val), else the detector set's image
            pairs += [(lab, f"{L}/labels/{split}/{nm}.txt"), (own if os.path.exists(own) else f"{D}/yolo/images/{split}/{nm}.png", f"{L}/images/{split}/{nm}.png")]
    with ThreadPoolExecutor(64) as ex:
        list(ex.map(lambda p: shutil.copyfile(*p) if not os.path.exists(p[1]) else None, pairs))
    open(f"{L}/data.yaml", "w").write(f"path: {L}\ntrain: images/train\nval: images/val\nnames:\n" +
                                      "".join(f"  {i}: {c}\n" for i, c in enumerate(SEG_CLASSES)))
    return L, len(pairs) // 2


@APP.function(image=image, volumes={D: VOL}, timeout=3600, cpu=8)
def seg_keep_val():
    """Copy the held-out images the segmentation labels refer to into yoloseg/images/val, so that seg_eval no longer
    depends on the detector set (/data/yolo), which other stages delete and rebuild."""
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    VOL.reload()
    os.makedirs(f"{D}/yoloseg/images/val", exist_ok=True)
    names = [os.path.basename(f)[:-4] for f in glob.glob(f"{D}/yoloseg/labels/val/*.txt")]
    todo = [(f"{D}/yolo/images/val/{n}.png", f"{D}/yoloseg/images/val/{n}.png") for n in names]
    todo = [t for t in todo if os.path.exists(t[0]) and not os.path.exists(t[1])]
    with ThreadPoolExecutor(32) as ex:
        list(ex.map(lambda t: shutil.copyfile(*t), todo))
    VOL.commit()
    return dict(labels=len(names), copied=len(todo), kept=len(glob.glob(f"{D}/yoloseg/images/val/*.png")))


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=24 * 3600, memory=32768, cpu=8)
def train_seg(run: str, model: str, epochs: int, imgsz: int, batch: int, fraction: float = 1.0, resume: bool = False):
    """resume=True continues a run that was cut off, from its last saved epoch (runs/<run>/weights/last.pt) to the
    epoch count it was started with."""
    from ultralytics import YOLO
    VOL.reload()
    L, n = _seg_local()
    m = YOLO(f"{D}/runs/{run}/weights/last.pt" if resume else model)
    m.add_callback("on_fit_epoch_end", lambda tr: VOL.commit() if (tr.epoch + 1) % 2 == 0 else None)
    if resume:
        m.train(resume=True)
    else:
        _seg_fit(m, L, run, epochs, imgsz, batch, fraction)
    VOL.commit()
    return _seg_summary(run, model, imgsz, n, L)


def _seg_fit(m, L, run, epochs, imgsz, batch, fraction):
    # mask_ratio=2: masks at half resolution instead of a quarter, because walls are only a few pixels thick
    m.train(data=f"{L}/data.yaml", epochs=epochs, imgsz=imgsz, batch=batch, project=f"{D}/runs", name=run, exist_ok=True,
            fliplr=0.5, flipud=0.5, mosaic=0.3, degrees=0, scale=0.3, translate=0.1, hsv_h=0.0, hsv_s=0.4, hsv_v=0.35,
            close_mosaic=10, patience=15, fraction=fraction, workers=8, plots=False, mask_ratio=2, max_det=600)


def _seg_summary(run, model, imgsz, n, L):
    from ultralytics import YOLO
    best = YOLO(f"{D}/runs/{run}/weights/best.pt")
    res = best.val(data=f"{L}/data.yaml", imgsz=imgsz, split="val", plots=False, max_det=600)
    out = dict(run=run, model=model, imgsz=imgsz, images=n,
               mask_map50={SEG_CLASSES[int(c)]: round(float(v), 3) for c, v in zip(res.seg.ap_class_index, res.seg.ap50)},
               box_map50={SEG_CLASSES[int(c)]: round(float(v), 3) for c, v in zip(res.box.ap_class_index, res.box.ap50)})
    json.dump(out, open(f"{D}/runs/{run}/summary.json", "w"))
    VOL.commit()
    return out


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=2 * 3600, memory=32768, cpu=8)
def seg_eval(run: str, imgsz: int, limit: int = 200, fill_conf: float = 0.0):
    """What the outlines are for, measured on held-out plans: room areas against the annotated outlines, and wall
    coverage. Per room: matched if IoU >= 0.7; area error = |predicted - annotated| / annotated. Per plan: error of the
    summed indoor area. Walls: pixel IoU of all predicted wall masks against all annotated walls."""
    import cv2, numpy as np, sys
    from ultralytics import YOLO
    sys.path.insert(0, "/root")
    from drawqa.segment import pick_rooms
    VOL.reload()
    L, _ = _seg_local(("val",))
    m = YOLO(f"{D}/runs/{run}/weights/best.pt")
    files = sorted(f for f in glob.glob(f"{L}/images/val/*.png") if not f.endswith("_s.png"))[:limit]
    room_err, room_signed, half, plan_err, wall_iou, found, total = [], [], [], [], [], 0, 0
    for f in files:
        im = cv2.imread(f)
        h, w = im.shape[:2]
        gt = {0: [], 1: [], 2: []}
        for line in open(f.replace("/images/", "/labels/")[:-4] + ".txt"):
            v = line.split()
            gt[int(v[0])].append((np.array(v[1:], float).reshape(-1, 2) * [w, h]).astype(np.int32))
        # same settings as the local path (drawqa/segment.py): masks at network resolution, simplified outlines
        r = m.predict(im, imgsz=imgsz, conf=0.25, iou=0.6, max_det=600, retina_masks=False, verbose=False)[0]
        pred, cands = {0: [], 1: [], 2: []}, {1: [], 2: []}
        if r.masks is not None:
            for poly, c, sc in zip(r.masks.xy, r.boxes.cls.tolist(), r.boxes.conf.tolist()):
                if len(poly) < 3 or sc < (0.25 if int(c) == 0 else (fill_conf or 0.35)):
                    continue
                pts = cv2.approxPolyDP(np.array(poly, np.float32).reshape(-1, 1, 2), 1.5 if int(c) == 0 else 2.5, True).reshape(-1, 2)
                if len(pts) >= 3 and int(c) == 0:
                    pred[0].append(pts.astype(np.int32))
                elif len(pts) >= 3:
                    cands[int(c)].append(dict(polygon=pts.tolist(), conf=sc))
        for c in (1, 2):  # the same keep rule as the local path (fill=0: only confident outlines)
            pred[c] = [np.int32(k["polygon"]) for k in pick_rooms(cands[c], (h, w), 0.35, fill_conf or None)]
        fill = lambda polys: cv2.fillPoly(np.zeros((h, w), np.uint8), polys, 1) if polys else np.zeros((h, w), np.uint8)
        gw, pw = fill(gt[0]), fill(pred[0])
        if gw.sum():
            wall_iou.append(float((gw & pw).sum()) / float((gw | pw).sum()))
        pm = [fill([p]).astype(bool) for p in pred[1]]
        g_area = p_area = 0.0
        for g in gt[1]:
            gm = fill([g]).astype(bool)
            total += 1
            g_area += gm.sum()
            best = max(((float((gm & q).sum()) / float((gm | q).sum()), q) for q in pm), key=lambda t: t[0], default=(0, None))
            if best[0] >= 0.7:
                found += 1
                room_err.append(abs(float(best[1].sum()) - float(gm.sum())) / float(gm.sum()))
                room_signed.append(float(best[1].sum()) / float(gm.sum()) - 1)
                half.append(len(plan_err) < len(files) // 2)  # first half of the plans: where the size correction is fitted
        p_area = float(sum(q.sum() for q in pm))
        if g_area:
            plan_err.append(abs(p_area - g_area) / g_area)
    q = lambda a, p: round(float(np.percentile(a, p)), 4) if a else None
    share = lambda a, t: round(float(np.mean([x <= t for x in a])), 3) if a else None
    # outlines run a little small: fit the median shortfall on the first half of the plans, score the second half with it
    fit = [v for v, h in zip(room_signed, half) if h]
    bias = float(np.median(fit)) if fit else 0.0
    rest = [abs((1 + v) / (1 + bias) - 1) for v, h in zip(room_signed, half) if not h]
    corrected = dict(bias_fitted_on_first_half=round(bias, 4), rooms_scored=len(rest), median=q(rest, 50), p90=q(rest, 90),
                     within_2pct=share(rest, 0.02), within_5pct=share(rest, 0.05))
    out = dict(run=run, fill_conf=fill_conf or None, plans=len(files), rooms=total, rooms_matched=round(found / max(total, 1), 3),
               room_area_signed=dict(median=q(room_signed, 50), p25=q(room_signed, 25), p75=q(room_signed, 75)),
               room_area_error_corrected=corrected,
               room_area_error=dict(median=q(room_err, 50), p90=q(room_err, 90), within_2pct=share(room_err, 0.02), within_5pct=share(room_err, 0.05)),
               plan_indoor_area_error=dict(median=q(plan_err, 50), p90=q(plan_err, 90), within_2pct=share(plan_err, 0.02), within_5pct=share(plan_err, 0.05)),
               wall_pixel_iou=dict(mean=round(float(np.mean(wall_iou)), 3) if wall_iou else None, p10=q(wall_iou, 10)))
    json.dump(out, open(f"{D}/runs/{run}/seg_eval.json", "w"))
    VOL.commit()
    return out


@APP.function(image=dl_image, volumes={D: VOL}, timeout=3 * 3600, cpu=4)
def fetch_junction():
    """Swiss plans (PDF+DXF+PNG) and BIM plan renders from the full Junction zip, kept on the Modal volume only."""
    import subprocess
    if os.path.exists(f"{D}/junction/.done"):
        return "already fetched"
    subprocess.run(["aria2c", "-x", "8", "-s", "8", "-k", "50M", "--max-tries=20", "--summary-interval=30",
                    "--console-log-level=warn", "-d", "/tmp", "-o", "j.zip", JUNCTION_ZIP], check=True)
    subprocess.run(["unzip", "-q", "-o", "/tmp/j.zip", "junction-dataset/data/swiss_dwellings/*", "junction-dataset/data/ifc_models/*.svg",
                    "junction-dataset/data/ifc_models/*.png", "junction-dataset/data/ifc_models/*.pdf",
                    "junction-dataset/questions.jsonl", "-d", f"{D}/junction"], check=True)
    open(f"{D}/junction/.done", "w").write("ok")
    VOL.commit()
    return subprocess.run(["bash", "-c", f"du -sh {D}/junction/junction-dataset/data/*; ls {D}/junction/junction-dataset/data/swiss_dwellings | wc -l"],
                          capture_output=True, text=True).stdout


def tiles(w, h, size=TILE, overlap=0.25):
    """Tile origins covering a w x h image; one tile if the image fits."""
    def axis(n):
        if n <= size:
            return [0]
        step = int(size * (1 - overlap))
        xs = list(range(0, n - size, step)) + [n - size]
        return sorted(set(xs))
    return [(x, y) for y in axis(h) for x in axis(w)]


def _swiss_one(args):
    """One Swiss plan -> overlapping tiles with labels (symbols only). Returns (name, split, class counts) or None."""
    import sys, cv2
    sys.path.insert(0, "/root")
    import swiss_labels
    dxf_path, split = args
    try:
        r = swiss_labels.boxes(dxf_path)
    except Exception:
        return None
    if r is None:
        return None
    png, w, h, bx, info = r
    img = cv2.imread(png)
    name = "swiss-" + os.path.basename(dxf_path)[:-4]
    n = {}
    for c, *_ in bx:
        n[c] = n.get(c, 0) + 1
    for ti, (tx, ty) in enumerate(tiles(w, h)):
        tw, th = min(TILE, w - tx), min(TILE, h - ty)
        lab = []
        for c, x0, y0, x1, y1 in bx:
            a0, b0, a1, b1 = max(x0 - 2, tx), max(y0 - 2, ty), min(x1 + 2, tx + tw), min(y1 + 2, ty + th)
            if a1 - a0 < 3 or b1 - b0 < 3 or (a1 - a0) * (b1 - b0) < 0.5 * (x1 - x0 + 4) * (y1 - y0 + 4):
                continue  # keep a box only where at least half of it lies inside the tile
            lab.append(f"{CLASSES.index(c)} {((a0 + a1) / 2 - tx) / tw:.6f} {((b0 + b1) / 2 - ty) / th:.6f} {(a1 - a0) / tw:.6f} {(b1 - b0) / th:.6f}")
        cv2.imwrite(f"{D}/yolo/images/{split}/{name}_t{ti}.png", img[ty:ty + th, tx:tx + tw])
        open(f"{D}/yolo/labels/{split}/{name}_t{ti}.txt", "w").write("\n".join(lab) + ("\n" if lab else ""))
        if split == "train" and lab and _seed(f"{name}_t{ti}") % 3 == 0:  # a third of the tiles also in another style
            cv2.imwrite(f"{D}/yolo/images/{split}/{name}_t{ti}_s.png", style_variant(img[ty:ty + th, tx:tx + tw], _seed(f"{name}_t{ti}") // 3))
            open(f"{D}/yolo/labels/{split}/{name}_t{ti}_s.txt", "w").write("\n".join(lab) + "\n")
    return name, split, n


@APP.function(image=image, volumes={D: VOL}, timeout=2 * 3600, cpu=8, memory=16384)
def convert_swiss(exclude: list, limit: int = 0):
    """Adds the Swiss plans to the YOLO dataset. `exclude` = preview file stems (kept as the Swiss test set)."""
    import collections, zlib
    from multiprocessing import Pool
    VOL.reload()
    for f in glob.glob(f"{D}/yolo/images/*/swiss-*") + glob.glob(f"{D}/yolo/labels/*/swiss-*"):
        os.remove(f)
    ex = set(exclude)
    site = lambda f: os.path.basename(f).split("_")[0]
    ex_sites = {e.split("_")[0] for e in ex}  # whole sites of the preview plans (other floors of a test building stay out too)
    files = sorted(glob.glob(f"{D}/junction/junction-dataset/data/swiss_dwellings/*.dxf"))
    kept = [f for f in files if os.path.basename(f)[:-4] not in ex and site(f) not in ex_sites]
    jobs = [(f, "val" if zlib.crc32(site(f).encode()) % 10 == 0 else "train") for f in (kept[:limit] if limit else kept)]  # split by site
    with Pool(8) as p:
        res = [r for r in p.map(_swiss_one, jobs, chunksize=4) if r]
    counts = collections.Counter()
    gt = {}  # kept in its own file so runs that train on apartment scans only are not tuned on Swiss plans
    for name, split, n in res:
        counts.update(n)
        gt[name] = dict(split=split, classes=n, keys=count_keys(n), errors=0, source="swiss",
                        image=f"{D}/junction/junction-dataset/data/swiss_dwellings/{name[6:]}.png")
    json.dump(gt, open(f"{D}/yolo/gt_extra.json", "w"))
    lists = write_lists()
    VOL.commit()
    return dict(files=len(files), excluded_preview=len(files) - len(kept), converted=len(res), failed=len(jobs) - len(res),
                val=sum(1 for r in res if r[1] == "val"), boxes=dict(counts), train_images=lists)


def _ifc_one(args):
    """One BIM plan export (SVG with a class per element + its PNG render) -> tiles with door/window labels."""
    import re, cv2
    import numpy as np
    svg_path, split = args
    png, pdf = svg_path[:-4] + ".png", svg_path[:-4] + ".pdf"
    t = open(svg_path, encoding="utf-8", errors="replace").read()
    vb = re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"', t)
    if os.path.exists(png):
        img = cv2.imread(png)
    elif os.path.exists(pdf):  # the plan PDF is the same drawing; render it at 150 dpi
        import pymupdf
        pm = pymupdf.open(pdf)[0].get_pixmap(matrix=pymupdf.Matrix(150 / 72, 150 / 72), alpha=False)
        img = np.frombuffer(pm.samples, np.uint8).reshape(pm.h, pm.w, pm.n)[:, :, :3][:, :, ::-1].copy()
    else:
        return None
    if vb is None or img is None:
        return None
    vx, vy, vw, vh = map(float, vb.groups())
    h, w = img.shape[:2]
    kx, ky = w / vw, h / vh
    bx = []
    for cls, body in re.findall(r'<g[^>]*class="(IfcDoor|IfcWindow)"[^>]*>(.*?)</g>', t, re.S):
        nums = [float(v) for d in re.findall(r' d="([^"]+)"', body) for v in re.findall(r"-?\d+\.?\d*(?:e-?\d+)?", d)]
        if len(nums) < 4:
            continue
        X, Y = nums[0::2], nums[1::2]
        x0, y0, x1, y1 = (min(X) - vx) * kx, (min(Y) - vy) * ky, (max(X) - vx) * kx, (max(Y) - vy) * ky
        if x1 - x0 < 3 and y1 - y0 < 3:
            continue
        bx.append(("door" if cls == "IfcDoor" else "window", x0, y0, x1, y1))
    name = "ifc-" + os.path.basename(svg_path)[:-4]
    n = {}
    for c, *_ in bx:
        n[c] = n.get(c, 0) + 1
    for ti, (tx, ty) in enumerate(tiles(w, h)):
        tw, th = min(TILE, w - tx), min(TILE, h - ty)
        lab = []
        for c, x0, y0, x1, y1 in bx:
            a0, b0, a1, b1 = max(x0 - 2, tx), max(y0 - 2, ty), min(x1 + 2, tx + tw), min(y1 + 2, ty + th)
            if a1 - a0 < 3 or b1 - b0 < 3 or (a1 - a0) * (b1 - b0) < 0.5 * (x1 - x0 + 4) * (y1 - y0 + 4):
                continue
            lab.append(f"{CLASSES.index(c)} {((a0 + a1) / 2 - tx) / tw:.6f} {((b0 + b1) / 2 - ty) / th:.6f} {(a1 - a0) / tw:.6f} {(b1 - b0) / th:.6f}")
        if not lab and ti % 3:
            continue  # keep only some empty tiles
        cv2.imwrite(f"{D}/yolo/images/{split}/{name}_t{ti}.png", img[ty:ty + th, tx:tx + tw])
        open(f"{D}/yolo/labels/{split}/{name}_t{ti}.txt", "w").write("\n".join(lab) + ("\n" if lab else ""))
        if split == "train" and lab and _seed(f"{name}_t{ti}") % 2 == 0:
            cv2.imwrite(f"{D}/yolo/images/{split}/{name}_t{ti}_s.png", style_variant(img[ty:ty + th, tx:tx + tw], _seed(f"{name}_t{ti}") // 2))
            open(f"{D}/yolo/labels/{split}/{name}_t{ti}_s.txt", "w").write("\n".join(lab) + "\n")
    return name, split, n, (w, h)


@APP.function(image=image, volumes={D: VOL}, timeout=2 * 3600, cpu=8, memory=16384)
def convert_ifc():
    """Adds BIM plan exports (third drawing style) to the YOLO dataset: doors and windows only."""
    import collections, zlib
    from multiprocessing import Pool
    VOL.reload()
    for f in glob.glob(f"{D}/yolo/images/*/ifc-*") + glob.glob(f"{D}/yolo/labels/*/ifc-*"):
        os.remove(f)
    files = sorted(glob.glob(f"{D}/junction/junction-dataset/data/ifc_models/*_plan-*.svg"))
    model = lambda f: os.path.basename(f).split("_plan-")[0]
    jobs = [(f, "val" if zlib.crc32(model(f).encode()) % 8 == 0 else "train") for f in files]
    with Pool(8) as p:
        res = [r for r in p.map(_ifc_one, jobs, chunksize=2) if r]
    counts = collections.Counter()
    for name, split, n, _ in res:
        counts.update(n)
    lists = write_lists()
    VOL.commit()
    return dict(svg_plans=len(files), converted=len(res), val=sum(1 for r in res if r[1] == "val"), boxes=dict(counts),
                sizes=[r[3] for r in res[:5]], train_images=lists)


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=24 * 3600, memory=32768, cpu=8)
def train(run: str, model: str, epochs: int, imgsz: int, batch: int, fraction: float = 1.0, mosaic: float = 0.5, data: str = "c5k",
          symbols_only: bool = False):
    from ultralytics import YOLO, RTDETR
    import subprocess
    VOL.reload()
    # the volume is a network disk; copy the dataset to the container's local disk so image loading is not the bottleneck
    L = "/tmp/yolo"
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    want = set()
    for split in ("train", "val"):  # only the files this run uses; the network disk serves ~25 files/s per thread
        for img in open(f"{D}/yolo/{split}_{data}.txt").read().split():
            want.add(img)
            want.add(img.replace("/images/", "/labels/")[:-4] + ".txt")
    want |= set(glob.glob(f"{D}/yolo/*.txt")) | set(glob.glob(f"{D}/yolo/*.yaml"))
    for d in ("images/train", "images/val", "labels/train", "labels/val"):
        os.makedirs(f"{L}/{d}", exist_ok=True)

    def copy(f):  # /data/yolo/... -> /tmp/yolo/...; a mixed set's /data/yolo_fpc/... -> /tmp/yolo_fpc/... (same rewrite)
        if os.path.exists(f):
            dst = L + f[len(f"{D}/yolo"):]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(f, dst)
    with ThreadPoolExecutor(64) as ex:
        list(ex.map(copy, sorted(want)))
    for f in glob.glob(f"{L}/*.txt") + glob.glob(f"{L}/*.yaml"):
        txt = open(f).read().replace(f"{D}/yolo", L)
        open(f, "w").write(txt)
    m = (RTDETR if "rtdetr" in model else YOLO)(model)
    m.add_callback("on_fit_epoch_end", lambda tr: VOL.commit() if (tr.epoch + 1) % 5 == 0 else None)  # keep checkpoints
    m.train(data=f"{L}/data_{data}.yaml", epochs=epochs, imgsz=imgsz, batch=batch, project=f"{D}/runs", name=run,
            exist_ok=True, fliplr=0.5, flipud=0.5, mosaic=mosaic, degrees=0, scale=float(os.environ.get("DRAWQA_SCALE", "0.3")), translate=0.1,
            save_period=int(os.environ.get("DRAWQA_SAVE_PERIOD", "-1")),
            hsv_h=0.0, hsv_s=0.4, hsv_v=0.35, close_mosaic=10, patience=20, fraction=fraction, workers=8, plots=False,
            classes=list(range(8)) if symbols_only else None)  # symbols_only: drop room/floor labels (classes 8-14)
    VOL.commit()
    best = (RTDETR if "rtdetr" in model else YOLO)(f"{D}/runs/{run}/weights/best.pt")
    res = best.val(data=f"{L}/data_{data}.yaml", imgsz=imgsz, split="val", plots=False)
    out = dict(run=run, model=model, imgsz=imgsz, data=data, map50=round(float(res.box.map50), 4), map5095=round(float(res.box.map), 4),
               per_class_map50={CLASSES[int(c)]: round(float(v), 3) for c, v in zip(res.box.ap_class_index, res.box.ap50)})
    json.dump(out, open(f"{D}/runs/{run}/summary.json", "w"))
    VOL.commit()
    return out


def _nms(dets, iou_thr=0.3):
    """Class-wise NMS over [(cls, [x0,y0,x1,y1], conf)], used to merge detections from overlapping tiles."""
    out = []
    for c in {d[0] for d in dets}:
        cand = sorted((d for d in dets if d[0] == c), key=lambda d: -d[2])
        keep = []
        for d in cand:
            b = d[1]
            for k in keep:
                q = k[1]
                iw, ih = min(b[2], q[2]) - max(b[0], q[0]), min(b[3], q[3]) - max(b[1], q[1])
                if iw > 0 and ih > 0:
                    inter = iw * ih
                    small = min((b[2] - b[0]) * (b[3] - b[1]), (q[2] - q[0]) * (q[3] - q[1]))
                    union = (b[2] - b[0]) * (b[3] - b[1]) + (q[2] - q[0]) * (q[3] - q[1]) - inter
                    if inter / union > iou_thr or inter / max(small, 1e-9) > 0.5:  # same object seen in two tiles (thin boxes overlap loosely)
                        break
            else:
                keep.append(d)
        out += keep
    return out


def _fuse(runs, iou_thr=0.35):
    """Merge detections of the same image seen in several orientations.

    runs = list (one per orientation) of [(cls, box, conf)] already mapped back to the original frame. Boxes of one
    class that overlap are one object; its score is the mean confidence over ALL orientations (0 where it was not
    found), so an object seen in every orientation scores high and a one-off false alarm scores low."""
    n = len(runs)
    clusters = []  # [cls, [boxes], [confs], set(orientation)]
    for oi, dets in enumerate(runs):
        for c, b, s in sorted(dets, key=lambda d: -d[2]):
            best = None
            for cl in clusters:
                if cl[0] != c or oi in cl[3]:
                    continue
                q = cl[1][0]
                iw, ih = min(b[2], q[2]) - max(b[0], q[0]), min(b[3], q[3]) - max(b[1], q[1])
                if iw <= 0 or ih <= 0:
                    continue
                ab, aq = (b[2] - b[0]) * (b[3] - b[1]), (q[2] - q[0]) * (q[3] - q[1])
                iou = iw * ih / (ab + aq - iw * ih)
                if (iou > iou_thr or iw * ih / max(min(ab, aq), 1e-9) > 0.6) and (best is None or iou > best[0]):
                    best = (iou, cl)
            if best:
                best[1][1].append(b); best[1][2].append(s); best[1][3].add(oi)
            else:
                clusters.append([c, [b], [s], {oi}])
    out = []
    for c, boxes, confs, _ in clusters:
        w = sum(confs)
        box = [round(sum(b[i] * s for b, s in zip(boxes, confs)) / w, 1) for i in range(4)]
        out.append((c, box, round(w / n, 3)))
    return out


MIN_DOOR_PX = 30  # below this (at network scale) symbols are too small to read from the whole sheet


def _detect_one(m, im, imgsz, tile="auto"):
    """One image in one orientation -> [(cls, box, conf)].

    The detector was trained on whole apartment plans fitted to the network size, and on native-resolution tiles of
    big multi-apartment sheets. So: look at the whole sheet first; only if its doors come out tiny (or none are found)
    is the sheet cut into tiles at native resolution."""
    conv = lambda r, dx=0, dy=0: [(CLASSES[int(c)], [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy], float(sc))
                                  for b, c, sc in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())]
    kw = dict(imgsz=imgsz, conf=0.05, iou=0.6, max_det=600, verbose=False)
    h, w = im.shape[:2]
    whole = conv(m.predict(im, **kw)[0])
    if tile == "never" or max(w, h) <= TILE * 1.2:
        return whole
    if tile == "auto":
        k = imgsz / max(w, h)
        doors = sorted(max(b[2] - b[0], b[3] - b[1]) * k for c, b, sc in whole if c == "door" and sc >= 0.3)
        if len(doors) >= 2 and doors[len(doors) // 2] >= MIN_DOOR_PX:
            return whole
    dets, tl = [], tiles(w, h)
    crops = [im[y:y + TILE, x:x + TILE] for x, y in tl]
    for i in range(0, len(crops), 8):
        for (x, y), r in zip(tl[i:i + 8], m.predict(crops[i:i + 8], **kw)):
            dets += conv(r, x, y)
    return _nms(dets)


def _load_models(run):
    """'a11m' or 'a11m,a26m' -> list of loaded models (several = an ensemble, fused like extra orientations).
    'e27@epoch6' loads a saved checkpoint of a run instead of its best.pt (DRAWQA_SAVE_PERIOD keeps them)."""
    from ultralytics import YOLO, RTDETR
    out = []
    for r in run.split(","):
        r, _, ck = r.partition("@")
        out.append((RTDETR if "rtdetr" in open(f"{D}/runs/{r}/args.yaml").read() else YOLO)(f"{D}/runs/{r}/weights/{ck or 'best'}.pt"))
    return out


def _detect(m, imgs, imgsz, tta):
    """Boxes per image, conf >= 0.05, as [(cls name, [x0,y0,x1,y1], conf)].

    m = one model or a list of models. tta=True: run the four 90-degree orientations (plans come in any
    orientation). Every model x orientation is one "view"; views are fused, and an object's score is its mean
    confidence over all views."""
    import cv2, numpy as np
    models = m if isinstance(m, list) else [m]
    out = []
    for im in imgs:
        if isinstance(im, str):
            im = cv2.imread(im)
        h, w = im.shape[:2]
        runs = []
        for mdl in models:
            for k in (range(4) if tta else [0]):  # k quarter-turns counter-clockwise
                rot = np.ascontiguousarray(np.rot90(im, k)) if k else im
                back = []
                for c, (x0, y0, x1, y1), s in _detect_one(mdl, rot, imgsz):
                    b = [x0, y0, x1, y1] if k == 0 else [w - y1, x0, w - y0, x1] if k == 1 else [w - x1, h - y1, w - x0, h - y0] if k == 2 else [y0, h - x1, y1, h - x0]
                    back.append((c, b, s))
                runs.append(back)
        dets = runs[0] if len(runs) == 1 else _fuse(runs)
        out.append([(c, [round(v, 1) for v in b], round(s, 3)) for c, b, s in dets if s >= 0.05])
    return out


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=2 * 3600, memory=32768)
def tune(run: str, imgsz: int, tta: bool = False, data: str = "c5k"):
    """Pick, per class, the confidence threshold that most often gives the exact count on held-out plans."""
    import numpy as np
    from ultralytics import YOLO, RTDETR
    VOL.reload()
    m = _load_models(run)
    gt = json.load(open(f"{D}/yolo/gt_counts.json"))
    if data == "all" and os.path.exists(f"{D}/yolo/gt_extra.json"):
        gt.update(json.load(open(f"{D}/yolo/gt_extra.json")))
    gt = {k: v for k, v in gt.items() if v["split"] == "val"}
    names = sorted(gt)
    src_of = lambda n: gt[n].get("source", "c5k")
    dets = _detect(m, [gt[n].get("image") or f"{D}/yolo/images/val/{n}.png" for n in names], imgsz, tta)
    import sys
    sys.path.insert(0, "/root")
    from drawqa.postprocess import merge_row  # same merging code the question engine uses
    grid = [round(float(t), 3) for t in np.arange(0.05, 0.951, 0.025)]
    # per class: confidence threshold x merge rule ("off" = count boxes as they are; None = merge nested boxes;
    # a number = also merge boxes in a row along a wall, gap up to that many wall thicknesses)
    OPTIONS = {c: ["off", None] for c in CLASSES[:8]}
    OPTIONS["window"] += [0.0, 0.5, 1.0, 2.0, 3.0]
    OPTIONS["door"] += [0.0, 0.5]
    thr, cls_acc, rules = {}, {}, {}
    per_img = {c: [sorted(((b, s) for cc, b, s in d if cc == c), key=lambda v: -v[1]) for d in dets] for c in CLASSES}

    def count(c, i, t, opt):
        bs = [v for v in per_img[c][i] if v[1] >= t]
        return len(bs) if opt == "off" or len(bs) < 2 else len(merge_row(bs, opt))

    for c in CLASSES:
        best = (-1, 0.25, "off")
        for opt in OPTIONS.get(c, ["off"]):
            for t in grid:
                ok = sum(count(c, i, t, opt) == gt[n]["classes"].get(c, 0) for i, n in enumerate(names))
                if ok > best[0] or (ok == best[0] and best[2] == opt and abs(t - 0.4) < abs(best[1] - 0.4)):
                    best = (ok, t, opt)
        thr[c], cls_acc[c] = best[1], round(best[0] / len(names), 4)
        if best[2] != "off":
            rules[c] = dict(gap=best[2])
    key_ok, src_ok = {}, {}
    SWISS_KEYS = ("windows", "doors", "wc", "sinks", "shower_bath")  # Swiss plans have labels for symbols only
    for i, n in enumerate(names):
        cnt = {c: count(c, i, thr[c], rules[c]["gap"] if c in rules else "off") for c in CLASSES}
        for k, v in count_keys(cnt).items():
            if src_of(n) != "c5k" and k not in SWISS_KEYS:
                continue
            for store, key in ((key_ok, k), (src_ok, f"{src_of(n)}:{k}")):
                a = store.setdefault(key, [0, 0])
                a[0] += v == gt[n]["keys"][k]
                a[1] += 1
    out = dict(run=run, tta=tta, data=data, plans=len(names), thresholds=thr, rules=rules, class_exact=cls_acc,
               key_exact={k: round(a / b, 4) for k, (a, b) in key_ok.items()},
               source_key_exact={k: f"{a}/{b}" for k, (a, b) in sorted(src_ok.items())},
               overall_exact=round(sum(a for a, _ in key_ok.values()) / sum(b for _, b in key_ok.values()), 4))
    os.makedirs(f"{D}/runs/{run.replace(',', '+')}", exist_ok=True)
    json.dump(out, open(f"{D}/runs/{run.replace(',', '+')}/tune{'_tta' if tta else ''}.json", "w"))
    VOL.commit()
    return out


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=3600, memory=16384)
def diagnose(run: str, imgsz: int, cls: str, tta: bool = False, n_images: int = 6):
    """Held-out apartment scans: match predicted boxes of one class to the true boxes and say how they differ."""
    import cv2, collections
    from ultralytics import YOLO, RTDETR
    VOL.reload()
    m = (RTDETR if "rtdetr" in open(f"{D}/runs/{run}/args.yaml").read() else YOLO)(f"{D}/runs/{run}/weights/best.pt")
    tj = f"{D}/runs/{run}/tune{'_tta' if tta else ''}.json"
    thr = json.load(open(tj))["thresholds"][cls] if os.path.exists(tj) else 0.4
    ci = CLASSES.index(cls)
    names = sorted(os.path.basename(f)[:-4] for f in glob.glob(f"{D}/yolo/images/val/*.png") if not os.path.basename(f).startswith(("swiss-", "ifc-")))

    def iou(a, b):
        iw, ih = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
        return 0 if iw <= 0 or ih <= 0 else iw * ih / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - iw * ih)

    touch = lambda a, b: min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
    st, worst = collections.Counter(), []
    for n in names:
        im = cv2.imread(f"{D}/yolo/images/val/{n}.png")
        h, w = im.shape[:2]
        G = []
        for line in open(f"{D}/yolo/labels/val/{n}.txt"):
            c, cx, cy, bw, bh = line.split()
            if int(c) == ci:
                cx, cy, bw, bh = float(cx) * w, float(cy) * h, float(bw) * w, float(bh) * h
                G.append([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2])
        dets = [(b, s) for c, b, s in _detect(m, [im], imgsz, tta)[0] if c == cls]
        Dk = [b for b, s in dets if s >= thr]
        used, tp = set(), 0
        for d in Dk:
            best = max(((iou(d, g), i) for i, g in enumerate(G) if i not in used), default=(0, -1))
            if best[0] >= 0.4:
                used.add(best[1])
                tp += 1
        missed = [g for i, g in enumerate(G) if i not in used]
        extra = [d for d in Dk if max((iou(d, g) for g in G), default=0) < 0.4]
        st["plans"] += 1
        st["exact count"] += len(Dk) == len(G)
        st["true"] += len(G)
        st["found"] += tp
        st["missed"] += len(missed)
        st["missed, but a box below the threshold is there"] += sum(1 for g in missed if any(iou(b, g) >= 0.4 for b, s in dets if s < thr))
        st["missed, but covered by a bigger/smaller counted box (split or merge)"] += sum(1 for g in missed if any(touch(d, g) for d in Dk))
        st["extra"] += len(extra)
        st["extra, overlapping a true one (split or merge)"] += sum(1 for d in extra if any(touch(d, g) for g in G))
        st["extra, nothing there"] += sum(1 for d in extra if not any(touch(d, g) for g in G))
        if len(Dk) != len(G):
            worst.append((abs(len(Dk) - len(G)), n, len(G), len(Dk)))
    imgs = []
    for _, n, g_n, d_n in sorted(worst, reverse=True)[2:2 + n_images]:
        im = cv2.imread(f"{D}/yolo/images/val/{n}.png")
        h, w = im.shape[:2]
        k = 1400 / max(h, w)
        im = cv2.resize(im, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)
        for line in open(f"{D}/yolo/labels/val/{n}.txt"):
            c, cx, cy, bw, bh = line.split()
            if int(c) == ci:
                cx, cy, bw, bh = float(cx) * w * k, float(cy) * h * k, float(bw) * w * k, float(bh) * h * k
                cv2.rectangle(im, (int(cx - bw / 2) - 5, int(cy - bh / 2) - 5), (int(cx + bw / 2) + 5, int(cy + bh / 2) + 5), (0, 170, 0), 2)
        for c, b, sc in _detect(m, [cv2.imread(f"{D}/yolo/images/val/{n}.png")], imgsz, tta)[0]:
            if c == cls and sc >= 0.15:
                col = (255, 0, 0) if sc >= thr else (0, 140, 255)
                cv2.rectangle(im, (int(b[0] * k), int(b[1] * k)), (int(b[2] * k), int(b[3] * k)), col, 2)
                cv2.putText(im, f"{sc:.2f}", (int(b[0] * k), max(12, int(b[1] * k) - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        imgs.append((f"{n}_true{g_n}_pred{d_n}.jpg", cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()))
    return dict(cls=cls, threshold=thr, stats=dict(st)), imgs


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=3600, memory=16384)
def swiss_debug(run: str, imgsz: int, n: int = 2):
    """Held-out Swiss plans: true vs predicted door counts, which reading path was used, and one overlay image."""
    import cv2
    VOL.reload()
    m = _load_models(run)[0]
    gt = {k: v for k, v in json.load(open(f"{D}/yolo/gt_extra.json")).items() if v["split"] == "val"}
    rows, overlay = [], None
    for name in sorted(gt)[:n]:
        im = cv2.imread(gt[name]["image"])
        h, w = im.shape[:2]
        kw = dict(imgsz=imgsz, conf=0.05, iou=0.6, max_det=600, verbose=False)
        r = m.predict(im, **kw)[0]
        whole = [(CLASSES[int(c)], b, float(sc)) for b, c, sc in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())]
        k = imgsz / max(w, h)
        dsz = sorted(max(b[2] - b[0], b[3] - b[1]) * k for c, b, sc in whole if c == "door" and sc >= 0.3)
        auto = _detect_one(m, im, imgsz, "auto")
        tiled = _detect_one(m, im, imgsz, "always")
        cnt = lambda dets, t: sum(1 for c, b, sc in dets if c == "door" and sc >= t)
        rows.append(dict(plan=name[6:36], size=[w, h], true_doors=gt[name]["classes"].get("door", 0),
                         whole_doors_at_0_35=cnt(whole, 0.35), median_door_px_whole=round(dsz[len(dsz) // 2], 1) if dsz else None,
                         auto_at_0_35=cnt(auto, 0.35), tiled_at_0_25=cnt(tiled, 0.25), tiled_at_0_35=cnt(tiled, 0.35), tiled_at_0_5=cnt(tiled, 0.5)))
        if overlay is None:
            for c, b, sc in tiled:
                if c == "door" and sc >= 0.2:
                    cv2.rectangle(im, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (255, 0, 0) if sc >= 0.35 else (0, 140, 255), 2)
                    cv2.putText(im, f"{sc:.2f}", (int(b[0]), int(b[1]) - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            # true door boxes from the tile labels are not kept per sheet; redraw from the label generator
            import sys
            sys.path.insert(0, "/root")
            import swiss_labels
            P = [b for c, b, sc in tiled if c == "door" and sc >= 0.35]
            missing = []
            for c, x0, y0, x1, y1 in swiss_labels.boxes(gt[name]["image"][:-4] + ".dxf")[3]:
                if c == "door":
                    hit = any(min(x1, q[2]) > max(x0, q[0]) - 4 and min(y1, q[3]) > max(y0, q[1]) - 4 for q in P)
                    cv2.rectangle(im, (int(x0) - 6, int(y0) - 6), (int(x1) + 6, int(y1) + 6), (0, 170, 0) if hit else (0, 0, 255), 2 if hit else 5)
                    if not hit:
                        missing.append([round(x0), round(y0), round(x1), round(y1)])
            rows.append(dict(missing_true_doors=missing))
            overlay = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
    return rows, overlay


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=3600, memory=16384)
def predict(run: str, items: list, imgsz: int, tta: bool = False):
    import cv2, numpy as np
    from ultralytics import YOLO, RTDETR
    VOL.reload()
    m = _load_models(run)
    imgs = [cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) for _, data in items]
    dets = _detect(m, imgs, imgsz, tta)
    return [dict(path=name, model=f"yolo:{run}", boxes=[dict(cls=c, bbox=b, conf=s) for c, b, s in d]) for (name, _), d in zip(items, dets)]


def _space_one(folder):
    from xml.dom import minidom
    try:
        dom = minidom.parse(f"{D}/cubicasa5k{folder}model.svg")
    except Exception:
        return []
    return [(g.getAttribute("class")[6:], _text(g)) for g in dom.getElementsByTagName("g") if g.getAttribute("class").startswith("Space ")]


@APP.function(image=image, volumes={D: VOL}, timeout=3600, cpu=8, memory=8192)
def space_stats():
    """How CubiCasa labels rooms: count of every 'Space <kind>' class and the texts printed in the common ones.
    The result is also saved to the volume (space_stats.json), so a dropped connection does not lose it."""
    import collections
    from multiprocessing import Pool
    VOL.reload()
    folders = [l.strip() for f in ("train", "val", "test") for l in open(f"{D}/cubicasa5k/{f}.txt") if l.strip()]
    kinds, texts = collections.Counter(), collections.defaultdict(collections.Counter)
    with Pool(16) as p:
        for rows in p.imap_unordered(_space_one, folders, chunksize=16):
            for kind, lab in rows:
                kinds[kind] += 1
                texts[kind][lab.split()[0] if lab else ""] += 1
    out = dict(plans=len(folders), kinds=kinds.most_common(), texts={k: texts[k].most_common(14) for k, _ in kinds.most_common(24)})
    json.dump(out, open(f"{D}/space_stats.json", "w"))
    VOL.commit()
    return out


@APP.function(image=image, volumes={D: VOL}, timeout=24 * 3600)
def pipeline(run: str, model: str, epochs: int, imgsz: int, batch: int, ex: list, swiss_ex: list, rebuild: bool = True,
             extra: str = "", extra_max: int = 0):
    """Rebuild the dataset with the current CLASSES, train, tune. Progress goes to runs/<run>/pipeline.json on the volume.
    extra: another YOLO folder mixed into training (rebuild=False keeps /data/yolo as it is); tuning stays on 'all', the
    sources the answer key scores, so the result compares with earlier runs."""
    log = {}

    def note(k, v):
        log[k] = v
        os.makedirs(f"{D}/runs/{run}", exist_ok=True)
        json.dump(log, open(f"{D}/runs/{run}/pipeline.json", "w"), indent=1, default=str)
        VOL.commit()

    if rebuild:
        note("convert", convert.remote(ex))
        note("convert_swiss", convert_swiss.remote(swiss_ex))
        note("convert_ifc", convert_ifc.remote())
    data = "all"
    if extra:
        data = "all_" + os.path.basename(extra.rstrip("/")).replace("yolo_", "")
        note("mix", mix_lists.remote(extra, data, extra_max))
    note("train", train.remote(run, model, epochs, imgsz, batch, 1.0, 0.5, data, False))
    note("tune", tune.remote(run, imgsz, True, "all"))
    return log


@APP.function(image=image, volumes={D: VOL}, timeout=900)
def lists():
    VOL.reload()
    out = write_lists()
    VOL.commit()
    return out


@APP.function(image=image, volumes={D: VOL}, timeout=900)
def read_bytes(path: str):
    VOL.reload()
    return open(f"{D}/{path}", "rb").read() if os.path.exists(f"{D}/{path}") else None


@APP.function(image=image, volumes={D: VOL}, timeout=600)
def read_json(path: str):
    VOL.reload()
    return json.load(open(f"{D}/{path}")) if os.path.exists(f"{D}/{path}") else None


def _predict_local(run, imgsz, tta, files=None):
    files = files or sorted(glob.glob(str(ROOT / "junction-dataset-preview/data/cubicasa5k/*.png")))
    batches = [files[i:i + 40] for i in range(0, len(files), 40)]
    payloads = [[(os.path.basename(f), open(f, "rb").read()) for f in b] for b in batches]
    results = [r for p in payloads for r in predict.remote(run, p, imgsz, tta)]
    out = ROOT / "data" / "predictions" / f"yolo_{run.replace(',', '+').replace('@', '-')}{'_tta' if tta else ''}.jsonl"
    with open(out, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(results)} plans -> {out}")


@APP.local_entrypoint()
def main(stage: str = "smoke", run: str = "v1", epochs: int = 60, imgsz: int = 1280, model: str = "yolo11m.pt",
         batch: int = 16, fraction: float = 1.0, mosaic: float = 0.5, tta: bool = False, limit: int = 0, data: str = "c5k",
         symbols_only: bool = False):
    ex = preview_ids()
    print(f"{len(ex)} preview plans are excluded from training and tuning | GPU {GPU}")
    if stage in ("download", "smoke"):
        print(download.remote())
    if stage == "smoke":
        print(convert.remote(ex, limit=60))
        print(train.remote("smoke", "yolo11n.pt", 3, 640, 16, 1.0, mosaic))
        print(tune.remote("smoke", 640, False))
        _predict_local("smoke", 640, False, sorted(glob.glob(str(ROOT / "junction-dataset-preview/data/cubicasa5k/*.png")))[:5])
    if stage == "convert":
        print(json.dumps(convert.remote(ex, limit=limit), indent=1))
    if stage == "lists":
        print(lists.remote())
    if stage == "pipeline":  # e.g. --run d26l --model /data/runs/c26l/weights/best.pt --epochs 30
        swiss_ex = [os.path.basename(p)[:-4] for p in glob.glob(str(ROOT / "junction-dataset-preview/data/swiss_dwellings/*.dxf"))]
        # spawned, not awaited: progress and results are in runs/<run>/pipeline.json on the volume
        # DRAWQA_EXTRA=/data/yolo_fpc DRAWQA_EXTRA_MAX=12000 mixes another labelled set into training (use with DRAWQA_REBUILD=0)
        print("spawned", pipeline.spawn(run, model, epochs, imgsz, batch, ex, swiss_ex, os.environ.get("DRAWQA_REBUILD", "1") == "1",
                                        os.environ.get("DRAWQA_EXTRA", ""), int(os.environ.get("DRAWQA_EXTRA_MAX", 0))).object_id)
        return
    if stage == "mix_lists":  # dry run of the mixing: how many images each split would get
        print(json.dumps(mix_lists.remote(os.environ["DRAWQA_EXTRA"], "all_" + os.path.basename(os.environ["DRAWQA_EXTRA"].rstrip("/")).replace("yolo_", ""),
                                          int(os.environ.get("DRAWQA_EXTRA_MAX", 0))), indent=1))
    if stage == "space_stats":
        print(json.dumps(space_stats.remote(), indent=None))
    if stage == "convert_ifc":
        print(json.dumps(convert_ifc.remote(), indent=1))
    if stage == "fetch_junction":
        print(fetch_junction.remote())
    if stage == "convert_swiss":
        swiss_ex = [os.path.basename(p)[:-4] for p in glob.glob(str(ROOT / "junction-dataset-preview/data/swiss_dwellings/*.dxf"))]
        print(json.dumps(convert_swiss.remote(swiss_ex, limit=limit), indent=1))
    if stage == "train":
        print(json.dumps(train.remote(run, model, epochs, imgsz, batch, fraction, mosaic, data, symbols_only), indent=1))
    if stage == "tune" and os.environ.get("DRAWQA_SPAWN") == "1":
        # start it and leave: the result goes to runs/<run>/tune[_tta].json on the volume. A call made with .remote()
        # is cancelled when this machine loses its connection, even under --detach (that cut run d26l at epoch 19).
        print("spawned", tune.spawn(run, imgsz, tta, data).object_id)
        return
    if stage in ("tune", "train"):
        t = tune.remote(run, imgsz, tta, data)
        print(json.dumps(t, indent=1))
        json.dump(t, open(ROOT / "data" / "predictions" / f"yolo_{run.replace(',', '+').replace('@', '-')}{'_tta' if tta else ''}.thresholds.json", "w"), indent=1)
    if stage in ("predict", "train"):
        _predict_local(run, imgsz, tta)
    if stage == "diagnose":  # DRAWQA_CLS=window ; images go to DRAWQA_OUT
        stats, imgs = diagnose.remote(run, imgsz, os.environ.get("DRAWQA_CLS", "window"), tta)
        print(json.dumps(stats, indent=1))
        out = os.environ.get("DRAWQA_OUT", ".")
        os.makedirs(out, exist_ok=True)
        for name, data in imgs:
            open(os.path.join(out, name), "wb").write(data)
    if stage == "swiss_debug":
        rows, img = swiss_debug.remote(run, imgsz)
        for r in rows:
            print(r)
        open(os.environ.get("DRAWQA_OUT", "swiss_debug.jpg"), "wb").write(img)
    if stage == "weights":  # bring a trained detector to this machine: models/<run>.pt (+ thresholds);
        os.makedirs(ROOT / "models", exist_ok=True)   # DRAWQA_ACTIVATE=1 also makes it models/detector.pt, the default
        tag = run.replace(",", "+")
        data = read_bytes.remote(f"runs/{run}/weights/best.pt")
        t = read_json.remote(f"runs/{tag}/tune{'_tta' if tta else ''}.json")
        targets = [tag] + (["detector"] if os.environ.get("DRAWQA_ACTIVATE") == "1" else [])
        for name in targets:
            open(ROOT / "models" / f"{name}.pt", "wb").write(data)
            if t:
                json.dump(t, open(ROOT / "models" / f"{name}.thresholds.json", "w"), indent=1)
        print(f"models/{', '.join(targets)} <- run {run} ({len(data) / 1e6:.1f} MB), thresholds: {bool(t)}")
    if stage == "convert_seg":  # wall + room-outline labels; needs the detector set's images (stage convert) on the volume
        print(json.dumps(convert_seg.remote(ex, limit=limit), indent=1))
    if stage == "seg_keep_val":
        print(seg_keep_val.remote())
    if stage == "train_seg":  # e.g. --run seg1 --model yolo26m-seg.pt --epochs 40 --batch 8
        print(json.dumps(train_seg.remote(run, model, epochs, imgsz, batch, fraction), indent=1))
    if stage == "resume_seg":  # continue a cut-off run in the background: returns at once, the run goes on by itself
        call = train_seg.spawn(run, model, epochs, imgsz, batch, fraction, True)
        print("resumed in the background, call id", call.object_id)
    if stage in ("seg_eval", "train_seg"):
        ev = seg_eval.remote(run, imgsz, limit or 200, float(os.environ.get("DRAWQA_FILL", 0)))
        print(json.dumps(ev, indent=1))
        os.makedirs(ROOT / "models", exist_ok=True)  # kept next to the weights: drawqa/segment.py reads it
        json.dump(ev, open(ROOT / "models" / f"{run}.eval.json", "w"), indent=1)
    if stage == "gt":  # label counts per plan, to check the conversion against the dataset's answers
        json.dump(read_json.remote("yolo/gt_counts.json"), open(os.environ.get("DRAWQA_OUT", "gt_counts.json"), "w"))
    if stage == "summary":
        print(json.dumps(read_json.remote(f"runs/{run}/summary.json"), indent=1))
