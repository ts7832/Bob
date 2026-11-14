"""Window and door specialist detectors for scanned plans: bigger model, higher resolution, only these two classes.

Tests whether the window ceiling (about 50-56% of plans counted exactly) is a matter of model size and resolution.
Labels: the true window and door boxes of CubiCasa5k (rival/modal_rival.py --stage boxes), original-resolution images.
The 241 test plans are never trained or tuned on; thresholds are chosen on the validation plans.

    modal run rival/modal_win.py --stage prep
    RIVAL_GPU=H200 modal run --detach rival/modal_win.py --stage train --run w1 --model yolo26x.pt --imgsz 1600 --epochs 60
    modal run rival/modal_win.py --stage predict --run w1 --imgsz 1600     # -> answers/win-<run>.jsonl (windows and doors)
"""
import json, os, pathlib

import modal

APP = modal.App("bob-win")
VOL = modal.Volume.from_name("bob-data")
ROOT = pathlib.Path(__file__).resolve().parent.parent
D = "/data"
GPU = os.environ.get("RIVAL_GPU", "H200")
CLASSES = ["window", "door"]
image = (modal.Image.debian_slim(python_version="3.11").apt_install("libgl1", "libglib2.0-0")
         .pip_install("ultralytics==8.4.212", "opencv-python-headless", "numpy<2", "pillow")
         .add_local_file(str(ROOT / "eval" / "test_scans.jsonl"), "/root/test_scans.jsonl"))


def _one(args):
    import shutil
    name, split, b = args
    out = f"{D}/win/yolo"
    src = f"{D}/cubicasa5k/{name.rsplit('-', 1)[0]}/{name.rsplit('-', 1)[1]}/F1_original.png"
    try:
        shutil.copy(src, f"{out}/images/{split}/{name}.png")
    except Exception:
        return 0
    w, h = b["w"], b["h"]
    lines = []
    for ci, c in enumerate(CLASSES):
        for x0, y0, x1, y1 in b[c]:
            x0, y0, x1, y1 = max(0, x0 - 2), max(0, y0 - 2), min(w, x1 + 2), min(h, y1 + 2)
            if x1 - x0 >= 3 and y1 - y0 >= 3:
                lines.append(f"{ci} {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}")
    open(f"{out}/labels/{split}/{name}.txt", "w").write("\n".join(lines) + ("\n" if lines else ""))
    return 1


@APP.function(image=image, volumes={D: VOL}, timeout=3600, cpu=16, memory=16384)
def prep():
    from multiprocessing import Pool
    labels = json.load(open(f"{D}/rival/labels.json"))
    boxes = json.load(open(f"{D}/rival/boxes.json"))
    out = f"{D}/win/yolo"
    for s in ("train", "val", "test"):
        os.makedirs(f"{out}/images/{s}", exist_ok=True)
        os.makedirs(f"{out}/labels/{s}", exist_ok=True)
    jobs = [(n, labels[n]["split"], boxes[n]) for n in boxes if n in labels]
    with Pool(16) as p:
        n = sum(p.imap_unordered(_one, jobs, chunksize=16))
    open(f"{out}/data.yaml", "w").write(f"path: {out}\ntrain: images/train\nval: images/val\nnames:\n  0: window\n  1: door\n")
    VOL.commit()
    return dict(images=n, splits={s: len(os.listdir(f"{out}/images/{s}")) for s in ("train", "val", "test")})


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=10 * 3600, cpu=16, memory=65536)
def train(run: str, model: str, imgsz: int, epochs: int, batch: int, seed: int = 0):
    from ultralytics import YOLO
    m = YOLO(model)
    m.train(data=f"{D}/win/yolo/data.yaml", epochs=epochs, imgsz=imgsz, batch=batch, project=f"{D}/win/runs", name=run, exist_ok=True, seed=seed,
            mosaic=0.3, close_mosaic=10, fliplr=0.5, flipud=0.5, degrees=0, scale=0.25, hsv_h=0.0, hsv_s=0.2, hsv_v=0.2, workers=14,
            patience=100, plots=False, cos_lr=True, save_period=10)
    VOL.commit()
    return "done"


@APP.function(image=image, volumes={D: VOL}, gpu="L40S", timeout=3600, cpu=8, memory=32768)
def predict(run: str, imgsz: int, weights: str = "best"):
    """Boxes on validation and test plans; per class the confidence threshold that counts most validation plans exactly."""
    import glob
    from ultralytics import YOLO
    VOL.reload()
    m = YOLO(f"{D}/win/runs/{run}/weights/{weights}.pt")
    labels = json.load(open(f"{D}/rival/labels.json"))
    det = {}
    for split in ("val", "test"):
        files = sorted(glob.glob(f"{D}/win/yolo/images/{split}/*.png"))
        for i in range(0, len(files), 8):
            for f, r in zip(files[i:i + 8], m.predict(files[i:i + 8], imgsz=imgsz, conf=0.05, iou=0.5, augment=False, verbose=False)):
                det[os.path.basename(f)[:-4]] = [(int(c), round(float(s), 3), [round(float(v), 1) for v in b])
                                                for b, c, s in zip(r.boxes.xyxy.cpu().tolist(), r.boxes.cls.cpu().tolist(), r.boxes.conf.cpu().tolist())]
    rule = {}
    for ci, c in enumerate(CLASSES):
        val = [(n, labels[n]["answers"][c]) for n, l in labels.items() if l["split"] == "val" and c in l["answers"] and n in det]
        best = max(((sum(sum(1 for k, s, _ in det[n] if k == ci and s >= t) == a for n, a in val) / max(1, len(val)), t)
                    for t in [x / 100 for x in range(10, 80, 2)]))
        rule[c] = dict(threshold=best[1], val_exact=round(best[0], 4))
    test = {n: dict(boxes=det[n]) for n, l in labels.items() if l["split"] == "test" and n in det}
    json.dump(dict(rule=rule, det=det), open(f"{D}/win/runs/{run}/pred.json", "w"))
    VOL.commit()
    return dict(rule=rule, test=test)


@APP.local_entrypoint()
def main(stage: str = "prep", run: str = "w1", model: str = "yolo26x.pt", imgsz: int = 1600, epochs: int = 60, batch: int = 8, seed: int = 0,
         weights: str = "best"):
    if stage == "prep":
        print(prep.remote())
    elif stage == "train":
        print("started", train.spawn(run, model, imgsz, epochs, batch, seed).object_id, f"-> /win/runs/{run}")
    elif stage == "predict":
        r = predict.remote(run, imgsz, weights)
        print(r["rule"])
        with open(ROOT / "answers" / f"win-{run}.jsonl", "w") as f:
            for q in (json.loads(l) for l in open(ROOT / "eval" / "test_scans.jsonl")):
                n = os.path.basename(q["path"])[:-4]
                if q["kind"] in CLASSES and n in r["test"]:
                    ci, t = CLASSES.index(q["kind"]), r["rule"][q["kind"]]["threshold"]
                    bs = [(s, b) for k, s, b in r["test"][n]["boxes"] if k == ci and s >= t]
                    f.write(json.dumps(dict(id=q["id"], answer=len(bs), confidence=r["rule"][q["kind"]]["val_exact"], tier="detected",
                                            method=f"win:{run} specialist detector", evidence=[b for _, b in bs], cost_usd=0.0)) + "\n")
        print("wrote", ROOT / "answers" / f"win-{run}.jsonl")
