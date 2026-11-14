"""Crop checker: "how many separate windows are in this group of boxes?" (0 to 6), a typed multiple-choice model.

The detector's window errors on scans are mostly side-by-side windows: two under one box, or extra boxes on one
window. This model looks at the cut-out of a group of touching boxes and gives a probability for each count.
Trained on true window boxes of plans outside the test set (rival/modal_rival.py --stage boxes); groups are jittered,
shifted and mixed with door boxes and random regions so it also learns "nothing here" and "part of a window".

    modal run rival/modal_crop.py --run crop1 --epochs 12       # trains, then counts windows on the 241 test plans
Output: answers/crop-<run>.windows.json  {plan: {count, groups: [{box, probs}]}}
"""
import json, os, pathlib

import modal

APP = modal.App("bob-crop")
VOL = modal.Volume.from_name("bob-data")
ROOT = pathlib.Path(__file__).resolve().parent.parent
D = "/data"
GPU = os.environ.get("RIVAL_GPU", "L40S")
S, NCLS, MARGIN, GAP = 128, 7, 10, 3       # crop size; classes 0..6; margin and touching gap in px of the 1280 image
image = (modal.Image.debian_slim(python_version="3.11").apt_install("libgl1", "libglib2.0-0")
         .pip_install("torch==2.5.1", "torchvision==0.20.1", "timm==1.0.12", "huggingface_hub==0.26.5", "safetensors",
                      "opencv-python-headless", "numpy<2", "pillow")
         .add_local_file(str(ROOT / "data" / "predictions" / "yolo_c26l_tta.jsonl"), "/root/detector.jsonl"))


def groups(bs, g=GAP):
    """Indices of boxes joined when they touch (within g px)."""
    n, par = len(bs), list(range(len(bs)))

    def f(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    for i in range(n):
        for j in range(i):
            a, b = bs[i], bs[j]
            if not (a[2] + g < b[0] or b[2] + g < a[0] or a[3] + g < b[1] or b[3] + g < a[1]):
                par[f(i)] = f(j)
    out = {}
    for i in range(n):
        out.setdefault(f(i), []).append(i)
    return list(out.values())


def union(bs):
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


def cut(im, box):
    """Region + margin, long side scaled to S, centred on a white square. im: HxWx3 uint8 of the 1280 image."""
    import cv2, numpy as np
    h, w = im.shape[:2]
    x0, y0, x1, y1 = [int(round(v)) for v in (box[0] - MARGIN, box[1] - MARGIN, box[2] + MARGIN, box[3] + MARGIN)]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    out = np.full((S, S, 3), 255, np.uint8)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return out
    c = im[y0:y1, x0:x1]
    k = S / max(c.shape[:2])
    c = cv2.resize(c, (max(1, int(c.shape[1] * k)), max(1, int(c.shape[0] * k))), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    oy, ox = (S - c.shape[0]) // 2, (S - c.shape[1]) // 2
    out[oy:oy + c.shape[0], ox:ox + c.shape[1]] = c
    return out


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=3 * 3600, cpu=12, memory=32768)
def run(run: str, epochs: int, min_conf: float = 0.2):
    import random
    import cv2, numpy as np, timm, torch, torch.nn.functional as F
    labels = json.load(open(f"{D}/rival/labels.json"))
    boxes = json.load(open(f"{D}/rival/boxes.json"))
    sc = lambda n: 1280 / max(boxes[n]["w"], boxes[n]["h"])
    W = {n: [[v * sc(n) for v in b] for b in boxes[n]["window"]] for n in boxes}          # true windows, 1280 px
    DR = {n: [[v * sc(n) for v in b] for b in boxes[n]["door"] + boxes[n]["doorway"]] for n in boxes}
    inside = lambda n, r: sum(1 for b in W[n] if r[0] <= (b[0] + b[2]) / 2 <= r[2] and r[1] <= (b[1] + b[3]) / 2 <= r[3])

    def samples(n):
        """(region, count) pairs of one plan: true groups, parts of groups, single boxes, doors, shifted regions."""
        out = []
        ws = W[n]
        for g in groups(ws):
            out.append(union([ws[i] for i in g]))
            if len(g) > 1:
                k = random.randint(1, len(g) - 1)
                out.append(union([ws[i] for i in random.sample(g, k)]))            # a part of the group
        for b in random.sample(ws, min(len(ws), 3)):
            long_x = (b[2] - b[0]) >= (b[3] - b[1])
            d = (b[2] - b[0] if long_x else b[3] - b[1]) * random.uniform(0.3, 1.0) * random.choice([-1, 1])
            out.append([b[0] + d, b[1], b[2] + d, b[3]] if long_x else [b[0], b[1] + d, b[2], b[3] + d])   # slid along the wall
            out.append([b[0], b[1], (b[0] + b[2]) / 2, b[3]] if long_x else [b[0], b[1], b[2], (b[1] + b[3]) / 2])   # half a window
        for b in random.sample(DR[n], min(len(DR[n]), 2)):
            out.append(b)
        res = []
        for r in out:
            j = lambda: random.uniform(-4, 4)
            r = [r[0] + j(), r[1] + j(), r[2] + j(), r[3] + j()]
            if r[2] - r[0] > 3 and r[3] - r[1] > 3:
                res.append((r, min(inside(n, [r[0] - 2, r[1] - 2, r[2] + 2, r[3] + 2]), NCLS - 1)))
        return res

    mean, std = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)
    tens = lambda c: torch.from_numpy(((c.astype(np.float32) / 255 - mean) / std).transpose(2, 0, 1))

    class Train(torch.utils.data.Dataset):
        def __init__(self, names):
            self.names = names

        def __len__(self):
            return len(self.names)

        def __getitem__(self, i):
            n = self.names[i]
            im = cv2.cvtColor(cv2.imread(f"{D}/rival/img/{n}.png"), cv2.COLOR_BGR2RGB)
            xs, ys = [], []
            for r, c in samples(n)[:40]:
                crop = cut(im, r)
                if random.random() < 0.5:
                    crop = crop[:, ::-1]
                if random.random() < 0.5:
                    crop = crop[::-1]
                if random.random() < 0.5:
                    crop = crop.transpose(1, 0, 2)
                if random.random() < 0.3:
                    crop = np.repeat(cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_RGB2GRAY)[:, :, None], 3, 2)
                xs.append(tens(np.ascontiguousarray(crop)))
                ys.append(c)
            if not xs:
                return torch.zeros(0, 3, S, S), torch.zeros(0, dtype=torch.long)
            return torch.stack(xs), torch.tensor(ys)

    coll = lambda b: (torch.cat([x for x, _ in b]), torch.cat([y for _, y in b]))
    tr = sorted(n for n, l in labels.items() if l["split"] == "train" and n in W)
    va = sorted(n for n, l in labels.items() if l["split"] == "val" and n in W)
    dl = torch.utils.data.DataLoader(Train(tr), batch_size=12, shuffle=True, num_workers=11, collate_fn=coll, drop_last=True, persistent_workers=True)
    dv = torch.utils.data.DataLoader(Train(va), batch_size=12, num_workers=11, collate_fn=coll)
    model = timm.create_model("convnext_nano.in12k_ft_in1k", pretrained=True, num_classes=NCLS).cuda().to(memory_format=torch.channels_last)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=0.05)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-4, total_steps=epochs * len(dl), pct_start=0.1)
    log = []
    for ep in range(epochs):
        model.train()
        tot = n_b = 0
        for x, y in dl:
            x, y = x.cuda().to(memory_format=torch.channels_last), y.cuda()
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = F.cross_entropy(model(x).float(), y, label_smoothing=0.03)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot += loss.item()
            n_b += 1
        model.eval()
        ok = n = 0
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            for x, y in dv:
                p = model(x.cuda().to(memory_format=torch.channels_last)).argmax(1).cpu()
                ok += (p == y).sum().item()
                n += len(y)
        log.append(dict(epoch=ep + 1, loss=round(tot / n_b, 4), val_crop_accuracy=round(ok / max(1, n), 4)))
        print(log[-1], flush=True)
    os.makedirs(f"{D}/rival/runs/{run}", exist_ok=True)
    torch.save(model.state_dict(), f"{D}/rival/runs/{run}/crop.pt")

    # test plans: the detector's window boxes, grouped, each group counted by the checker
    out = {}
    model.eval()
    for line in open("/root/detector.jsonl"):
        d = json.loads(line)
        n = d["path"][:-4]
        if n not in boxes:
            continue
        k = sc(n)
        bs = [[v * k for v in b["bbox"]] for b in d["boxes"] if b["cls"] == "window" and b["conf"] >= min_conf]
        cf = [b["conf"] for b in d["boxes"] if b["cls"] == "window" and b["conf"] >= min_conf]
        im = cv2.cvtColor(cv2.imread(f"{D}/rival/img/{n}.png"), cv2.COLOR_BGR2RGB)
        gs = groups(bs)
        res = []
        if gs:
            x = torch.stack([tens(cut(im, union([bs[i] for i in g]))) for g in gs]).cuda().to(memory_format=torch.channels_last)
            with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
                p = (model(x).float().softmax(1) + model(x.flip(3)).float().softmax(1)) / 2
            for g, pr in zip(gs, p.cpu().tolist()):
                res.append(dict(box=[round(v / k, 1) for v in union([bs[i] for i in g])], boxes=len(g), max_conf=round(max(cf[i] for i in g), 3),
                                probs=[round(v, 4) for v in pr]))
        out[n] = dict(count=sum(max(range(NCLS), key=lambda c: r["probs"][c]) for r in res), groups=res)
    json.dump(dict(log=log, test=out), open(f"{D}/rival/runs/{run}/crop_test.json", "w"))
    VOL.commit()
    return dict(log=log, test=out)


@APP.local_entrypoint()
def main(run: str = "crop1", epochs: int = 12):
    r = globals()["run"].remote(run, epochs)
    json.dump(r["test"], open(ROOT / "answers" / f"crop-{run}.windows.json", "w"))
    Q = {os.path.basename(q["path"])[:-4]: q["answer"] for q in (json.loads(l) for l in open(ROOT / "eval" / "test_scans.jsonl"))
         if q["kind"] == "window" and q["lang"] == "en"}
    ok = sum(r["test"][n]["count"] == a for n, a in Q.items() if n in r["test"])
    print("last epochs:", r["log"][-2:])
    print(f"window count exactly right with the crop checker: {ok}/{len(Q)} = {100 * ok / len(Q):.1f}%  (detector 50.2%, box judge 54.4%)")
