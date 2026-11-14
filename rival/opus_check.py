"""Experiment: is Opus a useful second opinion on boxes the detector is unsure about?

    python rival/opus_check.py --n 150 --kind window

Takes detector boxes with a borderline confidence on the test scans, shows Opus a zoomed crop with the box drawn in
red, asks one yes/no question, and compares with the true answer (CubiCasa labels). Prints how often Opus is right,
how often the detector's own threshold is right on the same boxes, and the tokens used.
Key: ~/.config/bob/anthropic_key. Needs Pillow. Results: rival/out/opus_check_<kind>.json
"""
import base64, io, json, os, random, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARG = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
KIND, N = ARG("--kind", "window"), int(ARG("--n", "150"))
LO, HI, THR = float(ARG("--lo", "0.15")), float(ARG("--hi", "0.65")), float(ARG("--thr", "0.375"))
MODEL = ARG("--model", "claude-opus-5-5")
WHAT = {"window": "a window (a window symbol drawn in a wall: usually a thin rectangle or parallel lines across the wall's thickness)",
        "door": "a door with a door leaf (a swing arc or a leaf line in a wall opening; an open doorway without a leaf does not count, and a cupboard door does not count)"}


def ask(key, im, box):
    x0, y0, x1, y1 = box
    m = max(x1 - x0, y1 - y0) * 1.6 + 30
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    crop = im.crop((int(cx - m), int(cy - m), int(cx + m), int(cy + m)))
    k = 640 / crop.width
    crop = crop.resize((640, 640), Image.LANCZOS)
    ImageDraw.Draw(crop).rectangle([(x0 - cx + m) * k - 4, (y0 - cy + m) * k - 4, (x1 - cx + m) * k + 4, (y1 - cy + m) * k + 4], outline=(255, 0, 0), width=3)
    b = io.BytesIO()
    crop.save(b, "PNG")
    if os.environ.get("SAVE_CROP"):
        crop.save(os.environ["SAVE_CROP"])
    body = dict(model=MODEL, max_tokens=400, messages=[dict(role="user", content=[
        dict(type="image", source=dict(type="base64", media_type="image/png", data=base64.b64encode(b.getvalue()).decode())),
        dict(type="text", text=f"This is a crop of an architectural floor plan. Is the object inside the red box {WHAT[KIND]}? "
                               'Reply with JSON only: {"yes": true or false, "sure": a number from 0 to 1}')])])
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=120))
    t = "".join(c.get("text", "") for c in r["content"] if c.get("type") == "text")
    v = json.loads(t[t.index("{"):t.rindex("}") + 1])
    return bool(v["yes"]), float(v.get("sure", 0.5)), r["usage"]["input_tokens"], r["usage"]["output_tokens"]


def main():
    key = open(os.path.expanduser("~/.config/bob/anthropic_key")).read().strip()
    labels = json.load(open(ARG("--labels", "/tmp/claude-501/labels.json")))
    items = []
    for line in open(os.path.join(ROOT, "data", "predictions", "yolo_c26l_tta.jsonl")):
        d = json.loads(line)
        name = d["path"][:-4]
        lab = labels[name]
        k = 1280 / max(lab["w"], lab["h"])
        T = [(x / k, y / k) for x, y in lab["points"][KIND]]
        used = set()
        for b in sorted((b for b in d["boxes"] if b["cls"] == KIND), key=lambda b: -b["conf"]):
            x0, y0, x1, y1 = b["bbox"]
            hit = next((i for i, (tx, ty) in enumerate(T) if i not in used and x0 - 3 / k <= tx <= x1 + 3 / k and y0 - 3 / k <= ty <= y1 + 3 / k), None)
            if hit is not None:
                used.add(hit)
            if LO <= b["conf"] <= HI:
                items.append(dict(name=name, box=b["bbox"], conf=b["conf"], real=hit is not None))
    random.seed(7)
    random.shuffle(items)
    items = items[:N]
    imgs = {}

    def one(it):
        if it["name"] not in imgs:
            imgs[it["name"]] = Image.open(os.path.join(ROOT, "junction-dataset-preview", "data", "cubicasa5k", it["name"] + ".png")).convert("RGB")
        try:
            it["opus"], it["sure"], it["in"], it["out"] = ask(key, imgs[it["name"]], it["box"])
        except Exception as e:
            it["error"] = f"{type(e).__name__}: {e}"[:200]
        return it

    with ThreadPoolExecutor(4) as ex:
        done = list(ex.map(one, items))
    ok = [d for d in done if "opus" in d]
    n = len(ok)
    print(f"{n} boxes asked ({len(done) - n} errors); {sum(d['real'] for d in ok)} are real {KIND}s")
    if n:
        print(f"Opus right:               {sum(d['opus'] == d['real'] for d in ok)}/{n} = {100 * sum(d['opus'] == d['real'] for d in ok) / n:.1f}%")
        print(f"detector threshold right: {sum((d['conf'] >= THR) == d['real'] for d in ok)}/{n} = {100 * sum((d['conf'] >= THR) == d['real'] for d in ok) / n:.1f}%")
        print(f"always yes right:         {100 * sum(d['real'] for d in ok) / n:.1f}%")
        sure = [d for d in ok if d["sure"] >= 0.8]
        if sure:
            print(f"Opus when it says it is sure (>=0.8): {sum(d['opus'] == d['real'] for d in sure)}/{len(sure)} = {100 * sum(d['opus'] == d['real'] for d in sure) / len(sure):.1f}%")
        print(f"tokens: {sum(d['in'] for d in ok)} in, {sum(d['out'] for d in ok)} out; per box {sum(d['in'] for d in ok) // n} in")
    os.makedirs(os.path.join(ROOT, "rival", "out"), exist_ok=True)
    json.dump(done, open(os.path.join(ROOT, "rival", "out", f"opus_check_{KIND}.json"), "w"))
    for d in done:
        if "error" in d:
            print("error:", d["error"])
            break


if __name__ == "__main__":
    main()
