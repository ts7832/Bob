"""Our trained models on a Modal GPU, so reading a drawing does not depend on this computer's CPU.

    .venv/bin/modal deploy tools/modal_infer.py          # app 'drawqa-infer' (build once; redeploy after new weights)

Runs drawqa's own functions on images sent as PNG bytes: detect.detect (element detector), segment.outlines (walls and
room outlines), ocr.texts and textlayer.read_image (printed text, tiled on big sheets). Same code, same weights
(models/detector.pt, models/segmenter.pt and their side files) and the same library versions as the local venv, so the
results are the same; only the place changes. The client is drawqa/remote.py (DRAWQA_INFER=modal, the default).
The container stays up 20 minutes after the last call; drawqa.remote.warm() starts it before a demo.
"""
import os, pathlib
import modal

ROOT = pathlib.Path(__file__).resolve().parents[1]
GPU = os.environ.get("DRAWQA_INFER_GPU", "L4")
# DRAWQA_INFER_APP / DRAWQA_INFER_DETECTOR at deploy time give a second service with other detector weights, e.g.
#   DRAWQA_INFER_APP=drawqa-infer-e27 DRAWQA_INFER_DETECTOR=e27 modal deploy tools/modal_infer.py   (models/e27.pt)
# and the client picks it with DRAWQA_INFER_APP=drawqa-infer-e27.
DET = os.environ.get("DRAWQA_INFER_DETECTOR", "detector")
APP = modal.App(os.environ.get("DRAWQA_INFER_APP", "drawqa-infer"))
app = APP  # the name `modal deploy` looks for

image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("torch==2.9.1", "torchvision==0.24.1", "ultralytics==8.4.212", "numpy==2.3.4",
                 "opencv-python-headless==5.0.0.93", "rapidocr==3.10.0", "onnxruntime==1.32.0", "shapely==2.2.0", "pillow==12.0.0")
    .run_commands("python -c \"from rapidocr import RapidOCR; RapidOCR()\"")  # the OCR models download at build time, not per call
    .env({"DRAWQA_IN_MODAL": "1", "DRAWQA_WEIGHTS": "/root/models/detector.pt", "DRAWQA_SEG_WEIGHTS": "/root/models/segmenter.pt",
          "YOLO_CONFIG_DIR": "/tmp/ultralytics", "DRAWQA_TTA_MAX_TILES": "400"})
    .add_local_file(ROOT / "models" / f"{DET}.pt", "/root/models/detector.pt")
    .add_local_file(ROOT / "models" / f"{DET}.thresholds.json", "/root/models/detector.thresholds.json")
    .add_local_file(ROOT / "models" / "segmenter.pt", "/root/models/segmenter.pt")
    .add_local_file(ROOT / "models" / "segmenter.eval.json", "/root/models/segmenter.eval.json")
    .add_local_python_source("drawqa")
)


@APP.cls(image=image, gpu=GPU, cpu=8, memory=16384, timeout=1800, scaledown_window=1200, max_containers=2)
class Infer:
    @modal.enter()
    def load(self):
        import numpy as np
        from drawqa import detect, segment, ocr
        detect._load()
        segment._load()
        ocr.texts(np.full((64, 64, 3), 255, np.uint8))  # loads the OCR engine

    @staticmethod
    def _img(png):
        import cv2, numpy as np
        return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)

    @modal.method()
    def ping(self):
        import torch
        return dict(ok=True, cuda=torch.cuda.is_available(), gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)

    @modal.method()
    def detect(self, png, imgsz=1280, raw=False, tta=None):
        from drawqa import detect
        return detect.detect(self._img(png), imgsz=imgsz, raw=raw, tta=tta)

    @modal.method()
    def outlines(self, png, imgsz=1280):
        from drawqa import segment
        return segment.outlines(self._img(png), imgsz=imgsz)

    @modal.method()
    def outlines_tiled(self, png, tile=1280, overlap=0.25):
        from drawqa import segment
        return segment.outlines_tiled(self._img(png), tile=tile, overlap=overlap)

    @modal.method()
    def ocr(self, png):
        from drawqa import ocr
        return ocr.texts(self._img(png))

    @modal.method()
    def textlayer(self, png):
        from drawqa import textlayer
        return textlayer.read_image(self._img(png))
