"""Small test of an open vision model on Modal: how well does it answer count questions on scanned plans?

Runs the model on the 8 plans of data/predictions/frontier_blind_sample.json (all English questions for those
plans), so the score is comparable with the blind frontier-model sample. Also measures speed.

usage (from repo root):
  .venv/bin/modal run tools/modal_vlm_test.py --model Qwen/Qwen3-VL-8B-Instruct                         # L40S
  DRAWQA_GPU=A100-80GB .venv/bin/modal run tools/modal_vlm_test.py --model Qwen/Qwen3-VL-32B-Instruct
Writes data/predictions/vlm_<model>.json
"""
import json, os, pathlib, time

import modal

APP = modal.App("drawqa-vlm-test")
VOL = modal.Volume.from_name("drawqa-models", create_if_missing=True)
ROOT = pathlib.Path(__file__).resolve().parent.parent
M = "/models"

GPU = os.environ.get("DRAWQA_GPU", "L40S")  # one GPU type per run: Modal validates every function in the app,
                                            # and some GPU types need a payment method on the account

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm==0.33.0", "huggingface_hub", "pillow")
    # vLLM's sampler needs prebuilt GPU kernels; without them it tries to compile and fails (no CUDA compiler here)
    .run_commands("vllm download-kernels", gpu=GPU)  # needs a GPU present to pick the right kernels
)

SYSTEM = ("You are reading a scanned architectural floor plan. Answer the question with a single integer and "
          "nothing else. Count carefully over the whole image; the plan may be rotated or mirrored.")


@APP.function(image=image, volumes={M: VOL}, timeout=3600, cpu=4)
def download(model: str):
    from huggingface_hub import snapshot_download
    dst = f"{M}/{model.replace('/', '--')}"
    if not os.path.exists(dst + "/.done"):
        snapshot_download(model, local_dir=dst)
        open(dst + "/.done", "w").write("ok")
        VOL.commit()
    return dst


def _run(model_dir, items, long_side, system=SYSTEM, max_tokens=16):
    import base64, io
    from PIL import Image
    from vllm import LLM, SamplingParams

    t0 = time.time()
    llm = LLM(model=model_dir, max_model_len=8192, limit_mm_per_prompt={"image": 1}, gpu_memory_utilization=0.93)
    load_s = time.time() - t0

    def conv(png, question):
        im = Image.open(io.BytesIO(png)).convert("RGB")
        s = long_side / max(im.size)
        if s > 1:  # small scans: upscale so symbols are not a few pixels wide
            im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        return [{"role": "system", "content": system},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                             {"type": "text", "text": question}]}]

    convs = [conv(png, q) for _, png, q in items]
    sp = SamplingParams(temperature=0, max_tokens=max_tokens)
    t0 = time.time()
    outs = llm.chat(convs, sp)
    batch_s = time.time() - t0
    t0 = time.time()
    n_seq = min(8, len(convs))
    for c in convs[:n_seq]:  # one question at a time, as in a live Q&A
        llm.chat([c], sp)
    seq_s = (time.time() - t0) / n_seq
    return dict(load_s=round(load_s, 1), batch_s=round(batch_s, 1), seq_s_per_query=round(seq_s, 2),
                prompt_tokens=[len(o.prompt_token_ids) for o in outs],
                finish=[o.outputs[0].finish_reason for o in outs],
                answers=[o.outputs[0].text.strip() for o in outs])


@APP.function(image=image, volumes={M: VOL}, gpu=GPU, timeout=1800)
def run(model_dir: str, items: list, long_side: int, system: str = SYSTEM, max_tokens: int = 16):
    return _run(model_dir, items, long_side, system, max_tokens)


GROUND_SYSTEM = "You are reading a scanned architectural floor plan. The plan may be rotated or mirrored."
# question pattern -> (key, engine class or None, what to mark)
TARGETS = [
    ("How many windows", "windows", "window", "window (a window opening in a wall)"),
    ("How many doors", "doors", "door", "door (a doorway fitted with a door leaf, usually drawn with a swing arc; not open doorways without a leaf)"),
    ("toilet bowls", "wc", "wc", "toilet bowl (WC seat)"),
    ("How many sinks", "sinks", "sink", "sink or wash basin (kitchen sinks and bathroom basins; a double-bowl sink is one object)"),
    ("showers and bathtubs", "shower_bath", "shower", "shower or bathtub"),
    ("How many floors", "floors", None, "separate floor (storey) drawing: one box around each complete floor plan shown on the sheet"),
    ("How many bedrooms", "bedrooms", None, "bedroom (a room labelled MH or makuuhuone): one box per room"),
    ("How many kitchens", "kitchens", None, "kitchen, kitchenette or open kitchen (labels such as K, KK, KEITTIÖ): one box per kitchen"),
    ("bathrooms or washrooms", "bathrooms", None, "bathroom or washroom (labels such as KH, KPH, PH, PESUH, SH), not separate WC rooms: one box per room"),
    ("separate WC rooms", "wc_rooms", None, "separate WC room (a toilet room labelled WC): one box per room"),
    ("How many saunas", "saunas", None, "sauna room (labelled S or SAUNA): one box per sauna"),
    ("How many balconies", "balconies", None, "balcony (labelled PARVEKE): one box per balcony"),
]


def ground_prompt(what):
    return (f"Locate every {what} in this floor plan. Output the bounding boxes as a JSON list like "
            '[{"bbox_2d": [x1, y1, x2, y2], "label": "..."}]. If there are none, output [].')


def parse_boxes(text):
    import re
    out = []
    for m in re.finditer(r'"bbox_2d"\s*:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]', text):
        out.append([float(g) for g in m.groups()])
    return out


@APP.local_entrypoint()
def main(model: str = "Qwen/Qwen3-VL-8B-Instruct", long_side: int = 1536, mode: str = "count"):
    gpu = GPU
    ds = ROOT / "junction-dataset-preview"
    plans = list(json.load(open(ROOT / "data/predictions/frontier_blind_sample.json")))
    Q = [json.loads(l) for l in open(ds / "questions.jsonl")]
    Q = [q for q in Q if q["path"] in plans and q["question"].startswith("How many")]
    if mode == "ground":
        return _ground(model, gpu, long_side, ds, Q)
    items = [(q["path"], open(ds / q["path"], "rb").read(), q["question"]) for q in Q]
    print(f"{len(items)} questions on {len(plans)} plans; model {model} on {gpu}")
    model_dir = download.remote(model)
    t0 = time.time()
    res = run.remote(model_dir, items, long_side)
    res["gpu_wall_s"] = round(time.time() - t0, 1)
    rows = []
    for q, a in zip(Q, res.pop("answers")):
        digits = "".join(ch for ch in a if ch.isdigit())
        pred = int(digits) if digits else None
        rows.append(dict(path=q["path"], question=q["question"], truth=q["answer"], raw=a, pred=pred, ok=pred == q["answer"]))
    res.update(model=model, gpu=gpu, long_side=long_side, n=len(rows), correct=sum(r["ok"] for r in rows), rows=rows)
    out = ROOT / "data" / "predictions" / f"vlm_{model.split('/')[-1]}.json"
    json.dump(res, open(out, "w"), indent=1)
    print(f"{res['correct']}/{res['n']} correct | load {res['load_s']}s | batch {res['batch_s']}s | "
          f"{res['seq_s_per_query']}s per single query | mean prompt tokens {sum(res['prompt_tokens']) // len(rows)} -> {out}")


def _ground(model, gpu, long_side, ds, Q):
    """The model marks every element with a box; code counts the boxes. Boxes are saved in the detector format."""
    from PIL import Image
    tgt = lambda q: next(t for t in TARGETS if t[0] in q["question"])
    items = [(q["path"], open(ds / q["path"], "rb").read(), ground_prompt(tgt(q)[3])) for q in Q]
    print(f"{len(items)} marking requests; model {model} on {gpu}")
    model_dir = download.remote(model)
    res = run.remote(model_dir, items, long_side, GROUND_SYSTEM, 2000)
    rows, per_plan = [], {}
    for q, a, fin in zip(Q, res.pop("answers"), res.pop("finish")):
        _, key, cls, _ = tgt(q)
        w, h = Image.open(ds / q["path"]).size
        boxes = [[b[0] * w / 1000, b[1] * h / 1000, b[2] * w / 1000, b[3] * h / 1000] for b in parse_boxes(a)]  # 0-1000 -> pixels
        rows.append(dict(path=q["path"], key=key, truth=q["answer"], pred=len(boxes), ok=len(boxes) == q["answer"],
                         truncated=fin == "length", boxes=[[round(v, 1) for v in b] for b in boxes], raw=a[:300]))
        per_plan.setdefault(q["path"], []).extend(dict(cls=cls or key, bbox=[round(v, 1) for v in b], conf=1.0) for b in boxes)
    tag = model.split("/")[-1]
    res.update(model=model, gpu=gpu, long_side=long_side, n=len(rows), correct=sum(r["ok"] for r in rows), rows=rows)
    json.dump(res, open(ROOT / "data" / "predictions" / f"vlm_ground_{tag}.json", "w"), indent=1)
    with open(ROOT / "data" / "predictions" / f"vlm_ground_{tag}.jsonl", "w") as f:  # same format as detector output
        for path, boxes in per_plan.items():
            f.write(json.dumps(dict(path=os.path.basename(path), model=f"vlm:{tag}", boxes=boxes)) + "\n")
    print(f"marking: {res['correct']}/{res['n']} counts correct | load {res['load_s']}s | batch {res['batch_s']}s | "
          f"truncated answers: {sum(r['truncated'] for r in rows)}")
