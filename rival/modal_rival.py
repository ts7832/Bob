"""Rival counting model for scanned floor plans, on Modal.

A single image model outputs one density map per question kind (window, door, toilet, ...). The count is the sum of
the map, the map itself shows what was counted, and a probability for every possible count comes from a spread
fitted on held-out plans. Labels: CubiCasa5k's SVG annotations for where things are (same counting rules as
tools/modal_train.py), the Junction answer key for how many. The 241 preview scans are never trained or tuned on.

    modal run rival/modal_rival.py --stage download
    modal run rival/modal_rival.py --stage prep
    modal run --detach rival/modal_rival.py --stage train --run r1 --epochs 30
    modal run rival/modal_rival.py --stage predict --run r1      # -> answers/rival-r1.jsonl
"""
import json, os, pathlib

import modal

APP = modal.App("bob-rival")
VOL = modal.Volume.from_name("bob-data")
ROOT = pathlib.Path(__file__).resolve().parent.parent
D = "/data"
GPU = os.environ.get("RIVAL_GPU", "L40S")
ZIP_URL = "https://zenodo.org/records/2613548/files/cubicasa5k.zip?download=1"
KINDS = ["window", "door", "toilet", "sink", "shower_bath", "floor", "bedroom", "kitchen", "bathroom", "wc_room", "balcony", "sauna"]
SIZE = 1280   # long side of the network input
STRIDE = 4    # density map is SIZE / STRIDE

dl_image = modal.Image.debian_slim(python_version="3.11").apt_install("aria2", "unzip")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0", "git")
    .pip_install("torch==2.5.1", "torchvision==0.20.1", "timm==1.0.12", "scikit-image", "svgpathtools",
                 "opencv-python-headless", "numpy<2", "pillow", "huggingface_hub==0.26.5", "safetensors")
    .run_commands("git clone --depth 1 https://github.com/CubiCasa/CubiCasa5k /root/c5k")
    .add_local_file(str(ROOT / "eval" / "test_scans.jsonl"), "/root/test_scans.jsonl")
    .add_local_file(str(ROOT / "eval" / "build_test_set.py"), "/root/build_test_set.py")
)


@APP.function(image=dl_image, volumes={D: VOL}, timeout=4 * 3600, cpu=4)
def download():
    """CubiCasa5k from Zenodo (5.2 GB), 8 ranges in parallel."""
    import subprocess
    if os.path.exists(f"{D}/cubicasa5k/train.txt"):
        return "already downloaded"
    subprocess.run(["aria2c", "-x", "8", "-s", "8", "-k", "50M", "--retry-wait=10", "--max-tries=20", "--summary-interval=60",
                    "--console-log-level=warn", "-d", "/tmp", "-o", "c5k.zip", ZIP_URL], check=True)
    subprocess.run(["unzip", "-q", "-o", "/tmp/c5k.zip", "-d", D], check=True)
    VOL.commit()
    return subprocess.run(["bash", "-c", f"ls {D}/cubicasa5k | head; wc -l {D}/cubicasa5k/*.txt; du -sh {D}/cubicasa5k"],
                          capture_output=True, text=True).stdout


FIXTURES = {"Toilet": "toilet", "Sink": "sink", "SideSink": "sink", "RoundSink": "sink", "CornerSink": "sink",
            "DoubleSink": "sink", "DoubleSinkRight": "sink", "Bathtub": "shower_bath", "BathtubRound": "shower_bath",
            "Shower": "shower_bath", "ShowerPlatform": "shower_bath", "SaunaStove": "stove", "SaunaBench": "bench",
            "SaunaBenchHigh": "bench", "SaunaBenchLow": "bench", "SaunaBenchMid": "bench"}


def _text(g):
    for t in g.getElementsByTagName("text"):
        s = "".join(n.data for n in t.childNodes if n.nodeType == n.TEXT_NODE).strip()
        if s:
            return s.upper()
    return ""


def _poly(g):
    pol = next((p for p in g.childNodes if p.nodeName == "polygon"), None)
    if pol is None:
        return [], []
    pts = [a.split(",") for a in pol.getAttribute("points").split() if "," in a]
    return [float(x) for x, _ in pts], [float(y) for _, y in pts]


def _inside(px, py, X, Y):
    inside, n = False, len(X)
    for i in range(n):
        x0, y0, x1, y1 = X[i], Y[i], X[(i + 1) % n], Y[(i + 1) % n]
        if (y0 > py) != (y1 > py) and px < x0 + (py - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _prep_one(folder):
    """One plan -> resized image on the volume + the point of every counted thing, per question kind."""
    import re, sys
    from xml.dom import minidom
    from PIL import Image
    sys.path.insert(0, "/root/c5k/floortrans/loaders")
    import svg_utils

    src = f"{D}/cubicasa5k{folder}"
    name = folder.strip("/").replace("/", "-")
    try:
        im = Image.open(src + "F1_original.png").convert("RGB")
        ws, hs = Image.open(src + "F1_scaled.png").size
        dom = minidom.parse(src + "model.svg")
    except Exception:
        return None
    w, h = im.size
    k = SIZE / max(w, h)
    fx, fy = w / ws * k, h / hs * k           # SVG coordinates -> resized image
    P = {kind: [] for kind in KINDS}
    stoves, benches, undecided, errors = [], [], [], 0
    mid = lambda X, Y: ((min(X) + max(X)) / 2, (min(Y) + max(Y)) / 2)
    is_wc = lambda lab: re.fullmatch(r"(INV\.?)? ?WC.*", lab) is not None
    for g in dom.getElementsByTagName("g"):
        cls, gid = g.getAttribute("class"), g.getAttribute("id")
        toks = cls.split()
        try:
            if gid == "Door":
                if len(toks) > 1 and toks[1] == "None":
                    continue                  # open doorway without a leaf is not counted
                X, Y = _poly(g)
                if X:
                    P["door"].append(mid(X, Y))
            elif gid == "Window":
                X, Y = _poly(g)
                if X:
                    P["window"].append(mid(X, Y))
            elif cls.startswith("FixedFurniture "):
                kind = FIXTURES.get(toks[-1])
                if kind is None:
                    continue
                _, _, X, Y = svg_utils.get_icon(g)
                if len(X) == 0:
                    continue
                m = mid(list(X), list(Y))
                (stoves if kind == "stove" else benches if kind == "bench" else P[kind]).append(m)
            elif cls.startswith("Space "):
                space, lab = cls[6:], _text(g)
                X, Y = _poly(g)
                if len(X) < 3:
                    continue
                room = ("bedroom" if space.startswith("Bedroom") else "kitchen" if space.startswith("Kitchen")
                        else ("wc_room" if is_wc(lab) else "bathroom") if space.startswith("Bath")
                        else "balcony" if space.startswith("Outdoor Balcony")
                        else "sauna" if space.startswith("Sauna") or lab in ("S", "SAUNA") else None)
                if room:
                    P[room].append(mid(X, Y))
                else:
                    undecided.append((X, Y))
            elif "Floorplan" in toks:
                X, Y = [], []
                for sub in g.getElementsByTagName("g"):
                    if sub.getAttribute("id") == "Wall" or sub.getAttribute("class").startswith("Space "):
                        x, y = _poly(sub)
                        X += x
                        Y += y
                if X:
                    P["floor"].append(mid(X, Y))
        except Exception:
            errors += 1
    # most saunas are annotated as an unnamed space: a space holding a stove or bench is the sauna
    for X, Y in undecided:
        if any(_inside(mx, my, X, Y) for mx, my in stoves + benches):
            P["sauna"].append(mid(X, Y))
    while len(P["sauna"]) < len(stoves):      # answer key: max(stoves, rooms, 1 if any bench)
        P["sauna"].append(stoves[len(P["sauna"])])
    if not P["sauna"] and benches:
        P["sauna"].append(benches[0])
    os.makedirs(f"{D}/rival/img", exist_ok=True)
    im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS).save(f"{D}/rival/img/{name}.png")
    return name, dict(w=w, h=h, points={kd: [[round(x * fx, 1), round(y * fy, 1)] for x, y in v] for kd, v in P.items()}, errors=errors)


@APP.function(image=image, volumes={D: VOL}, timeout=3600, cpu=16, memory=16384)
def prep(limit: int = 0):
    """All 5,000 plans -> /data/rival/img/*.png (long side 1280) and /data/rival/labels.json."""
    import sys, zlib
    from multiprocessing import Pool
    sys.path.insert(0, "/root")
    from build_test_set import kind_of
    folders = [l.strip() for f in ("train", "val", "test") for l in open(f"{D}/cubicasa5k/{f}.txt") if l.strip()]
    if limit:
        folders = folders[:limit]
    test = {os.path.basename(json.loads(l)["path"])[:-4] for l in open("/root/test_scans.jsonl")}
    answers = {}
    for line in open(f"{D}/junction-dataset/questions.jsonl"):
        q = json.loads(line)
        if q["path"].startswith("data/cubicasa5k/"):
            kd = kind_of(q["question"])
            if kd in KINDS:
                answers.setdefault(os.path.basename(q["path"])[:-4], {})[kd] = q["answer"]
    with Pool(16) as pool:
        done = [r for r in pool.imap_unordered(_prep_one, folders, chunksize=8) if r]
    labels, agree = {}, {kd: [0, 0] for kd in KINDS}
    for name, lab in done:
        lab["answers"] = answers.get(name, {})
        lab["split"] = "test" if name in test else "val" if zlib.crc32(name.encode()) % 15 == 0 else "train"
        for kd, a in lab["answers"].items():
            agree[kd][0] += len(lab["points"][kd]) == a
            agree[kd][1] += 1
        labels[name] = lab
    json.dump(labels, open(f"{D}/rival/labels.json", "w"))
    VOL.commit()
    splits = {s: sum(1 for l in labels.values() if l["split"] == s) for s in ("train", "val", "test")}
    return dict(plans=len(labels), of=len(folders), splits=splits, with_answers=sum(1 for l in labels.values() if l["answers"]),
                svg_count_equals_answer={kd: f"{a}/{n}" for kd, (a, n) in agree.items()})


def _boxes_one(folder):
    """True window and door boxes of one plan, in pixels of the original image (for the crop checker)."""
    from xml.dom import minidom
    from PIL import Image
    src = f"{D}/cubicasa5k{folder}"
    try:
        w, h = Image.open(src + "F1_original.png").size
        ws, hs = Image.open(src + "F1_scaled.png").size
        dom = minidom.parse(src + "model.svg")
    except Exception:
        return None
    sx, sy = w / ws, h / hs
    out = dict(w=w, h=h, window=[], door=[], doorway=[])
    for g in dom.getElementsByTagName("g"):
        gid = g.getAttribute("id")
        if gid not in ("Door", "Window"):
            continue
        X, Y = _poly(g)
        if len(X) < 2:
            continue
        toks = g.getAttribute("class").split()
        kind = "window" if gid == "Window" else "doorway" if len(toks) > 1 and toks[1] == "None" else "door"
        out[kind].append([round(min(X) * sx, 1), round(min(Y) * sy, 1), round(max(X) * sx, 1), round(max(Y) * sy, 1)])
    return folder.strip("/").replace("/", "-"), out


@APP.function(image=image, volumes={D: VOL}, timeout=3600, cpu=16, memory=16384)
def boxes():
    from multiprocessing import Pool
    folders = [l.strip() for f in ("train", "val", "test") for l in open(f"{D}/cubicasa5k/{f}.txt") if l.strip()]
    with Pool(16) as pool:
        done = dict(r for r in pool.imap_unordered(_boxes_one, folders, chunksize=16) if r)
    json.dump(done, open(f"{D}/rival/boxes.json", "w"))
    VOL.commit()
    return dict(plans=len(done), windows=sum(len(v["window"]) for v in done.values()), doors=sum(len(v["door"]) for v in done.values()))


def _model():
    import timm, torch
    import torch.nn as nn
    import torch.nn.functional as F

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = timm.create_model("convnext_tiny.in12k_ft_in1k", pretrained=True, features_only=True)
            ch = self.body.feature_info.channels()
            self.lat = nn.ModuleList(nn.Conv2d(c, 160, 1) for c in ch)
            self.smooth = nn.ModuleList(nn.Sequential(nn.Conv2d(160, 160, 3, padding=1), nn.GELU()) for _ in ch)
            self.head = nn.Sequential(nn.Conv2d(160, 160, 3, padding=1), nn.GELU(), nn.Conv2d(160, len(KINDS), 1))

        def forward(self, x):
            f = self.body(x)
            y = None
            for i in range(len(f) - 1, -1, -1):
                l = self.lat[i](f[i])
                y = l if y is None else l + F.interpolate(y, size=l.shape[-2:], mode="bilinear", align_corners=False)
                y = self.smooth[i](y)
            return self.head(y)       # density x 100, one map per kind, at 1/4 resolution
    return Net()


def _dataset(labels, names, train):
    import numpy as np, torch, cv2, random

    M = SIZE // STRIDE
    g = np.arange(-6, 7)
    kern = np.exp(-(g[:, None] ** 2 + g[None, :] ** 2) / (2 * 1.5 ** 2))
    kern /= kern.sum()
    mean, std = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)

    class DS(torch.utils.data.Dataset):
        def __len__(self):
            return len(names)

        def __getitem__(self, i):
            name = names[i]
            lab = labels[name]
            im = cv2.cvtColor(cv2.imread(f"{D}/rival/img/{name}.png"), cv2.COLOR_BGR2RGB)
            h, w = im.shape[:2]
            pts = {kd: np.array(v, np.float32).reshape(-1, 2) for kd, v in lab["points"].items()}
            if train:
                s = random.uniform(0.7, 1.0)   # scale jitter
                im = cv2.resize(im, (max(8, int(w * s)), max(8, int(h * s))), interpolation=cv2.INTER_AREA)
                pts = {kd: v * s for kd, v in pts.items()}
                h, w = im.shape[:2]
                if random.random() < 0.5:
                    im = im[:, ::-1]
                    for v in pts.values():
                        v[:, 0] = w - v[:, 0]
                if random.random() < 0.5:
                    im = im[::-1]
                    for v in pts.values():
                        v[:, 1] = h - v[:, 1]
                if random.random() < 0.5:      # transpose = with the flips, all 8 orientations
                    im = im.transpose(1, 0, 2)
                    pts = {kd: v[:, ::-1].copy() for kd, v in pts.items()}
                    h, w = w, h
                im = np.ascontiguousarray(im)
                if random.random() < 0.3:
                    im = np.repeat(cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)[:, :, None], 3, 2)
                if random.random() < 0.5:
                    im = np.clip(im.astype(np.float32) * random.uniform(0.8, 1.2) + random.uniform(-20, 20), 0, 255).astype(np.uint8)
                ox, oy = random.randint(0, SIZE - w), random.randint(0, SIZE - h)
            else:
                ox, oy = (SIZE - w) // 2, (SIZE - h) // 2
            canvas = np.full((SIZE, SIZE, 3), 255, np.uint8)
            canvas[oy:oy + h, ox:ox + w] = im
            dens = np.zeros((len(KINDS), M + 12, M + 12), np.float32)
            count = np.zeros(len(KINDS), np.float32)
            for c, kd in enumerate(KINDS):
                for x, y in pts[kd]:
                    cx, cy = int(round((x + ox) / STRIDE)), int(round((y + oy) / STRIDE))
                    if 0 <= cx < M and 0 <= cy < M:
                        dens[c, cy:cy + 13, cx:cx + 13] += kern
                count[c] = lab["answers"].get(kd, len(pts[kd]))
            x = torch.from_numpy(((canvas.astype(np.float32) / 255 - mean) / std).transpose(2, 0, 1))
            return x, torch.from_numpy(dens[:, 6:-6, 6:-6] * 100), torch.from_numpy(count), i
    return DS()


def _sums(model, loader, tta=True):
    """Predicted count (sum of each density map) for every plan in the loader."""
    import torch
    out = {}
    model.eval()
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        for x, _, _, idx in loader:
            x = x.cuda(non_blocking=True).to(memory_format=torch.channels_last)
            p = model(x).float()          # no clamp: training sums the raw map, so counting must too
            if tta:
                p = (p + model(x.flip(3)).float().flip(3)) / 2
            s = p.sum((2, 3)) / 100
            for i, v in zip(idx.tolist(), s.cpu().tolist()):
                out[i] = v
    return out


def _peaks(model, loader, tta=True, floor=0.8):
    """Per plan and kind: the sum of the map, and its local peaks as (value, x, y) in map cells, strongest first."""
    import torch, torch.nn.functional as F
    out = {}
    model.eval()
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        for x, _, _, idx in loader:
            x = x.cuda(non_blocking=True).to(memory_format=torch.channels_last)
            p = model(x).float()
            if tta:
                p = (p + model(x.flip(3)).float().flip(3)) / 2
            sums = (p.sum((2, 3)) / 100).cpu().tolist()
            sm = F.avg_pool2d(p, 3, stride=1, padding=1)                 # a little smoothing before looking for peaks
            top = (F.max_pool2d(sm, 7, stride=1, padding=3) == sm) & (sm > floor)
            for b, i in enumerate(idx.tolist()):
                per = []
                for c in range(p.shape[1]):
                    ys, xs = torch.nonzero(top[b, c], as_tuple=True)
                    v = sm[b, c, ys, xs]
                    order = torch.argsort(v, descending=True)[:200]
                    per.append([(round(v[j].item(), 2), int(xs[j]), int(ys[j])) for j in order])
                out[i] = dict(sums=sums[b], peaks=per)
    return out


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=6 * 3600, cpu=12, memory=32768)
def train(run: str, epochs: int, batch: int, lr: float, limit: int = 0):
    import time, torch, torch.nn.functional as F
    labels = json.load(open(f"{D}/rival/labels.json"))
    tr = sorted(n for n, l in labels.items() if l["split"] == "train")
    va = sorted(n for n, l in labels.items() if l["split"] == "val")
    if limit:
        tr, va = tr[:limit], va[:max(8, limit // 8)]
    out = f"{D}/rival/runs/{run}"
    os.makedirs(out, exist_ok=True)
    dl = torch.utils.data.DataLoader(_dataset(labels, tr, True), batch_size=batch, shuffle=True, num_workers=11, drop_last=True,
                                     pin_memory=True, persistent_workers=True)
    dv = torch.utils.data.DataLoader(_dataset(labels, va, False), batch_size=batch, num_workers=11, pin_memory=True)
    model = _model().cuda().to(memory_format=torch.channels_last)
    body = list(model.body.parameters())
    new = [p for n, p in model.named_parameters() if not n.startswith("body.")]
    opt = torch.optim.AdamW([dict(params=body, lr=lr), dict(params=new, lr=lr * 2)], weight_decay=0.05)   # new layers learn a little faster
    steps = epochs * len(dl)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[lr, lr * 2], total_steps=steps, pct_start=0.05)
    log, best, t0 = [], -1, time.time()
    for ep in range(epochs):
        model.train()
        tot = [0.0, 0.0]
        for x, dens, count, _ in dl:
            x = x.cuda(non_blocking=True).to(memory_format=torch.channels_last)
            dens, count = dens.cuda(non_blocking=True), count.cuda(non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                p = model(x).float()
            # density error summed over each map (a mean over 100,000 mostly empty pixels gives almost no signal)
            l_d = ((p - dens) ** 2).sum((2, 3)).mean() / 1000
            l_c = F.smooth_l1_loss(p.sum((2, 3)) / 100, count)   # no clamp here, so the count always has a gradient
            loss = l_d + 0.1 * l_c
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            tot[0] += l_d.item()
            tot[1] += l_c.item()
        sums = _sums(model, dv, tta=False)
        acc = {}
        for c, kd in enumerate(KINDS):
            rows = [(round(sums[i][c]), labels[n]["answers"][kd]) for i, n in enumerate(va) if kd in labels[n]["answers"]]
            acc[kd] = round(sum(a == b for a, b in rows) / max(1, len(rows)), 4)
        n_q = sum(len(labels[n]["answers"]) for n in va)
        overall = sum(round(sums[i][c]) == labels[n]["answers"][kd] for i, n in enumerate(va) for c, kd in enumerate(KINDS)
                      if kd in labels[n]["answers"]) / max(1, n_q)
        log.append(dict(epoch=ep + 1, density_loss=round(tot[0] / len(dl), 5), count_loss=round(tot[1] / len(dl), 4),
                        val_accuracy=round(overall, 4), by_kind=acc, minutes=round((time.time() - t0) / 60, 1)))
        print(json.dumps(log[-1]), flush=True)
        torch.save(model.state_dict(), f"{out}/last.pt")
        if overall > best:
            best = overall
            torch.save(model.state_dict(), f"{out}/best.pt")
        json.dump(dict(run=run, epochs=epochs, gpu=GPU, train=len(tr), val=len(va), best=best, log=log), open(f"{out}/log.json", "w"), indent=1)
        VOL.commit()
    return log[-1]


def _fit_spread(rows):
    """rows: (predicted sum, true count). Spread sigma = a + b * sum of a rounded bell curve, chosen for the best log-likelihood."""
    import math
    best = (1e18, 0.3, 0.0)
    for a in [0.08, 0.12, 0.16, 0.2, 0.25, 0.3, 0.4, 0.5, 0.7, 1.0]:
        for b in [0.0, 0.01, 0.02, 0.04, 0.06, 0.1, 0.15]:
            nll = -sum(math.log(max(_dist(s, a, b).get(int(t), 0.0), 1e-6)) for s, t in rows)
            if nll < best[0]:
                best = (nll, a, b)
    return best[1], best[2]


def _dist(s, a, b):
    """Probability of every count near the predicted sum s."""
    import math
    sig = a + b * max(s, 0)
    ks = range(max(0, int(s) - 6), int(s) + 8)
    w = {k: math.exp(-((k - s) ** 2) / (2 * sig * sig)) for k in ks}
    z = sum(w.values()) or 1.0
    return {k: v / z for k, v in w.items()}


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=3600, cpu=8, memory=16384)
def predict(run: str, weights: str = "best"):
    """Counts for the test plans, two ways from the same maps: the sum of the map, and the number of peaks above a
    threshold. Which way and which threshold is chosen per kind on the validation plans only."""
    import torch
    VOL.reload()
    labels = json.load(open(f"{D}/rival/labels.json"))
    model = _model().cuda().to(memory_format=torch.channels_last)
    model.load_state_dict(torch.load(f"{D}/rival/runs/{run}/{weights}.pt", map_location="cuda"))
    res = {}
    for split in ("val", "test"):
        names = sorted(n for n, l in labels.items() if l["split"] == split)
        dl = torch.utils.data.DataLoader(_dataset(labels, names, False), batch_size=8, num_workers=7)
        r = _peaks(model, dl)
        res[split] = {n: r[i] for i, n in enumerate(names)}
    THR = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    count = lambda r, c, t: sum(1 for v, _, _ in r["peaks"][c] if v >= t)
    rule, out = {}, {}
    for c, kd in enumerate(KINDS):
        val = [(r, labels[n]["answers"][kd]) for n, r in res["val"].items() if kd in labels[n]["answers"]]
        acc = {("sum", 0): sum(round(r["sums"][c]) == a for r, a in val) / max(1, len(val))}
        for t in THR:
            acc[("peaks", t)] = sum(count(r, c, t) == a for r, a in val) / max(1, len(val))
        how, t = max(acc, key=acc.get)
        # how often the chosen way is exactly right on validation plans, by size of the count: the first confidence
        hit = {}
        for r, a in val:
            k = round(r["sums"][c]) if how == "sum" else count(r, c, t)
            b = min(k, 12) // 3
            hit.setdefault(b, []).append(k == a)
        rule[kd] = dict(how=how, threshold=t, val_accuracy=round(acc[(how, t)], 4), val_sum=round(acc[("sum", 0)], 4),
                        by_size={b: round((sum(v) + 1) / (len(v) + 2), 3) for b, v in hit.items()})
    for n, r in res["test"].items():
        out[n] = {}
        for c, kd in enumerate(KINDS):
            ru = rule[kd]
            k = round(r["sums"][c]) if ru["how"] == "sum" else count(r, c, ru["threshold"])
            k = max(0, k)
            pts = [[x * STRIDE, y * STRIDE, v] for v, x, y in r["peaks"][c] if v >= (ru["threshold"] or 2.0)]
            out[n][kd] = dict(answer=k, confidence=ru["by_size"].get(min(k, 12) // 3, ru["val_accuracy"]), sum=round(r["sums"][c], 2), points=pts,
                              all_peaks=[[x * STRIDE, y * STRIDE, v] for v, x, y in r["peaks"][c]])   # every peak, for the box-level judge
    json.dump(dict(rule=rule, test=out), open(f"{D}/rival/runs/{run}/predictions.json", "w"))
    VOL.commit()
    return dict(rule=rule, test=out)


@APP.local_entrypoint()
def main(stage: str = "download", run: str = "r1", epochs: int = 30, batch: int = 8, limit: int = 0, lr: float = 1e-4,
         weights: str = "best"):
    if stage == "download":
        print(download.remote())
    elif stage == "prep":
        print(json.dumps(prep.remote(limit), indent=1))
    elif stage == "train":
        call = train.spawn(run, epochs, batch, lr, limit)   # spawn: survives a network drop on the laptop
        print("started", call.object_id, "-> progress in /rival/runs/%s/log.json on the volume" % run)
    elif stage == "boxes":
        print(boxes.remote())
    elif stage == "predict":
        r = predict.remote(run, weights)
        os.makedirs(ROOT / "answers", exist_ok=True)
        path = ROOT / "answers" / f"rival-{run}.jsonl"
        with open(path, "w") as f:
            for line in open(ROOT / "eval" / "test_scans.jsonl"):
                q = json.loads(line)
                a = r["test"].get(os.path.basename(q["path"])[:-4], {}).get(q["kind"])
                if a:
                    f.write(json.dumps(dict(id=q["id"], answer=a["answer"], confidence=a["confidence"], tier="detected",
                                            method=f"rival:{run} density map", evidence=a["points"], cost_usd=0.0)) + "\n")
        json.dump({n: {kd: v["all_peaks"] for kd, v in d.items()} for n, d in r["test"].items()}, open(ROOT / "answers" / f"rival-{run}.peaks.json", "w"))
        for kd, ru in r["rule"].items():
            print(kd, ru)
        print("wrote", path)
