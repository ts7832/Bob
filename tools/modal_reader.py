"""Room-name reader: a vision-language model on Modal that reads the room names and printed areas off a drawing image.

The element detector knows a fixed list of things. The names printed on a drawing are an open list (OLOHUONE, APUK-PUKUH,
Bedroom 2, ...), in any language. This service reads them, with the box around each name and the area printed next to it.

    DRAWQA_GPU=A100-80GB .venv/bin/modal deploy tools/modal_reader.py      # once; the model stays loaded 10 min after a call
    .venv/bin/python -m drawqa.reader <image>                              # call it (drawqa/reader.py)
Model: DRAWQA_READER_MODEL (default Qwen/Qwen3-VL-32B-Instruct, already on the volume 'drawqa-models').
Measured 2026-11-14 on two preview scans (before "skip appliance labels" was added to the prompt): on a hand-lettered
plan all 15 room names and all 15 printed areas were found (one name with a dropped letter); on a rotated plan all
room names were found, 4 of 19 texts had a wrong letter, and 7 were not room names (5 appliance labels, 2 floor
titles). 13 to 35 s per drawing on an A100-80GB once loaded. Cold start of the deployed service: about 11 minutes
(first live call 2026-11-14: 62 GB of weights read off the volume at ~70 s per shard), so warm it up before a demo;
it stays loaded for 10 minutes after the last call (scaledown_window).
"""
import json, os, re

import modal

APP = modal.App("drawqa-reader")
VOL = modal.Volume.from_name("drawqa-models", create_if_missing=True)
M = "/models"
GPU = os.environ.get("DRAWQA_GPU", "A100-80GB")
MODEL = os.environ.get("DRAWQA_READER_MODEL", "Qwen/Qwen3-VL-32B-Instruct")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm==0.33.0", "huggingface_hub", "pillow")
    .run_commands("vllm download-kernels", gpu=GPU)  # same image as tools/modal_vlm_test.py
    .env({"DRAWQA_READER_MODEL": MODEL})
)

SYSTEM = "You are reading a scanned architectural floor plan. The plan may be rotated or mirrored."
PROMPT = ("List every room or space name printed on this floor plan, in whatever language it is written, exactly as "
          "printed (for example OLOHUONE, KEITTIO, WC, Bedroom 2). Skip dimensions, notes, appliance and fixture labels, "
          'and title-block text. Output a JSON list like [{"bbox_2d": [x1, y1, x2, y2], "label": "...", "area": 25.0}] where '
          "bbox_2d is the box around the printed name and area is the room area printed next to it as a number, or null "
          "if none is printed.")


def parse_rooms(text, w, h):
    """Model output -> [{label, area, bbox in image pixels}] (the model answers in a 0-1000 grid)."""
    out = []
    for m in re.finditer(r'\{[^{}]*?"bbox_2d"\s*:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\][^{}]*?\}', text):
        try:
            d = json.loads(m.group(0))
        except Exception:
            continue
        b = [float(g) for g in m.groups()]
        area = d.get("area")
        if isinstance(area, str):
            mm = re.search(r"\d+(?:[.,]\d+)?", area)
            area = float(mm.group(0).replace(",", ".")) if mm else None
        label = str(d.get("label", "")).strip()
        if label:
            out.append(dict(label=label, area=float(area) if isinstance(area, (int, float)) else None,
                            bbox=[round(b[0] * w / 1000, 1), round(b[1] * h / 1000, 1), round(b[2] * w / 1000, 1), round(b[3] * h / 1000, 1)]))
    return out


@APP.cls(image=image, volumes={M: VOL}, gpu=GPU, timeout=1800, scaledown_window=600, max_containers=1)
class Reader:
    @modal.enter()
    def load(self):
        from huggingface_hub import snapshot_download
        from vllm import LLM
        self.model = os.environ.get("DRAWQA_READER_MODEL", MODEL)
        dst = f"{M}/{self.model.replace('/', '--')}"
        if not os.path.exists(dst + "/.done"):
            snapshot_download(self.model, local_dir=dst)
            open(dst + "/.done", "w").write("ok")
            VOL.commit()
        self.llm = LLM(model=dst, max_model_len=8192, limit_mm_per_prompt={"image": 1}, gpu_memory_utilization=0.93)

    @modal.method()
    def read(self, png: bytes, long_side: int = 1792):
        import base64, io, time
        from PIL import Image
        from vllm import SamplingParams
        t0 = time.time()
        im = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = im.size
        s = long_side / max(im.size)
        if s != 1 and (s > 1 or max(im.size) > 2 * long_side):  # small scans up, very large sheets down
            im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        conv = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": PROMPT}]}]
        o = self.llm.chat([conv], SamplingParams(temperature=0, max_tokens=3000))[0].outputs[0]
        return dict(model=self.model, seconds=round(time.time() - t0, 1), truncated=o.finish_reason == "length",
                    rooms=parse_rooms(o.text, w, h))
