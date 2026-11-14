"""Door / window / toilet / basin detector for CAD-style drawings (black lines on white), trained on FloorPlanCAD.

The Finnish-scan detector does not know this drawing style; FloorPlanCAD has 5,308 labelled CAD crops (20 m x 20 m at
1000 px, so 50 px per metre). The label names in the Hugging Face copy are scrambled; MAP below is the real meaning of
each, read off a contact sheet of crops. Images are inverted to black-on-white grey before training.

    modal run rival/modal_cad.py --stage prep
    modal run --detach rival/modal_cad.py --stage train --run cad1 --epochs 40
    modal run rival/modal_cad.py --stage detect --run cad1 --image <png> --px-per-m 116     # -> rival/out/<name>.cad.json/.png
Licences: FloorPlanCAD CC BY-NC 4.0; Ultralytics AGPL-3.0.
"""
import json, os, pathlib

import modal

APP = modal.App("bob-cad")
VOL = modal.Volume.from_name("bob-data")
D = "/data"
GPU = os.environ.get("RIVAL_GPU", "H100")
CLASSES = ["door", "double_door", "window", "toilet", "basin"]
MAP = {"double_door": "door", "sliding_door": "double_door", "stair": "window", "refrigerator": "window",
       "escalator": "toilet", "bath": "basin"}
PX_PER_M = 50
image = (modal.Image.debian_slim(python_version="3.11").apt_install("libgl1", "libglib2.0-0")
         .pip_install("ultralytics==8.4.212", "opencv-python-headless", "numpy<2", "pillow"))


@APP.function(image=image, volumes={D: VOL}, timeout=3600, cpu=8, memory=16384)
def prep():
    import collections, zlib
    import cv2
    S = json.load(open(f"{D}/fpcad/samples.json"))["samples"]
    out = f"{D}/cad/yolo"
    n = collections.Counter()
    for split in ("train", "val"):
        os.makedirs(f"{out}/images/{split}", exist_ok=True)
        os.makedirs(f"{out}/labels/{split}", exist_ok=True)
    for s in S:
        name = os.path.basename(s["filepath"])[:-4]
        split = "val" if zlib.crc32(name.split("-")[0].encode()) % 12 == 0 else "train"   # whole drawings stay together
        im = cv2.imread(f"{D}/fpcad/{s['filepath']}")
        if im is None:
            continue
        grey = 255 - im.max(axis=2)             # any coloured line on black -> dark line on white
        cv2.imwrite(f"{out}/images/{split}/{name}.png", grey)
        lines = []
        for d in (s.get("ground_truth") or {}).get("detections", []):
            c = MAP.get(d["label"])
            if c is None:
                continue
            x, y, w, h = d["bounding_box"]
            if w <= 0 or h <= 0:
                continue
            lines.append(f"{CLASSES.index(c)} {x + w / 2:.6f} {y + h / 2:.6f} {w:.6f} {h:.6f}")
            n[c] += 1
        open(f"{out}/labels/{split}/{name}.txt", "w").write("\n".join(lines) + ("\n" if lines else ""))
        n["images_" + split] += 1
    open(f"{out}/data.yaml", "w").write(f"path: {out}\ntrain: images/train\nval: images/val\nnames:\n" +
                                        "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASSES)))
    VOL.commit()
    return dict(n)


@APP.function(image=image, volumes={D: VOL}, gpu=GPU, timeout=4 * 3600, cpu=12, memory=32768)
def train(run: str, epochs: int, model: str = "yolo11m.pt", imgsz: int = 1024, batch: int = 16):
    from ultralytics import YOLO
    m = YOLO(model)
    m.train(data=f"{D}/cad/yolo/data.yaml", epochs=epochs, imgsz=imgsz, batch=batch, project=f"{D}/cad/runs", name=run, exist_ok=True,
            mosaic=0.5, fliplr=0.5, flipud=0.5, degrees=0, scale=0.3, hsv_h=0, hsv_s=0, hsv_v=0.2, workers=10, patience=100, plots=False)
    r = m.val(data=f"{D}/cad/yolo/data.yaml", imgsz=imgsz, plots=False)
    res = dict(map50=round(float(r.box.map50), 4), per_class={CLASSES[i]: round(float(v), 4) for i, v in zip(r.box.ap_class_index, r.box.ap50)})
    json.dump(res, open(f"{D}/cad/runs/{run}/summary.json", "w"))
    VOL.commit()
    return res


@APP.function(image=image, volumes={D: VOL}, gpu="L4", timeout=1800, cpu=4, memory=16384)
def detect(run: str, png: bytes, px_per_m: float, conf: float = 0.25):
    """Rescale the sheet to the training scale (50 px per metre), detect on overlapping 1024 px tiles, merge."""
    import io
    import numpy as np, torch
    from PIL import Image
    from torchvision.ops import nms
    from ultralytics import YOLO
    VOL.reload()
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(io.BytesIO(png)).convert("L")
    k = PX_PER_M / px_per_m
    im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    a = np.asarray(im)
    m = YOLO(f"{D}/cad/runs/{run}/weights/best.pt")
    T, step = 1024, 768
    boxes = []
    for y in range(0, max(1, a.shape[0] - 256), step):
        for x in range(0, max(1, a.shape[1] - 256), step):
            tile = a[y:y + T, x:x + T]
            if (tile < 128).mean() < 0.002:
                continue                                  # empty paper
            pad = np.full((T, T), 255, np.uint8)
            pad[:tile.shape[0], :tile.shape[1]] = tile
            r = m.predict(np.stack([pad] * 3, -1), imgsz=T, conf=conf, verbose=False)[0]
            for b, c, s in zip(r.boxes.xyxy.cpu().tolist(), r.boxes.cls.cpu().tolist(), r.boxes.conf.cpu().tolist()):
                boxes.append(([b[0] + x, b[1] + y, b[2] + x, b[3] + y], int(c), s))
    out = []
    for ci, cname in enumerate(CLASSES):
        bs = [b for b in boxes if b[1] == ci]
        if not bs:
            continue
        keep = nms(torch.tensor([b[0] for b in bs]), torch.tensor([b[2] for b in bs]), 0.3).tolist()
        out += [dict(cls=cname, conf=round(bs[i][2], 3), bbox=[round(v / k, 1) for v in bs[i][0]]) for i in keep]
    return out


@APP.local_entrypoint()
def main(stage: str = "prep", run: str = "cad1", epochs: int = 40, image: str = "", px_per_m: float = 50.0, conf: float = 0.25):
    if stage == "prep":
        print(prep.remote())
    elif stage == "train":
        print("started", train.spawn(run, epochs).object_id, f"-> /cad/runs/{run} on the volume")
    elif stage == "detect":
        p = pathlib.Path(os.path.expanduser(image))
        dets = detect.remote(run, p.read_bytes(), px_per_m, conf)
        out = pathlib.Path(__file__).resolve().parent / "out"
        out.mkdir(exist_ok=True)
        json.dump(dict(file=p.name, px_per_m=px_per_m, run=run, detections=dets), open(out / (p.stem + ".cad.json"), "w"))
        import collections
        print(collections.Counter(d["cls"] for d in dets))
