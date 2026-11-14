"""Our window judge alone, against our window judge that asks Opus only about the candidates it is unsure of.

    python eval/judge_plus_opus.py data/predictions/yolo_c26l_tta.jsonl answers/rival-r3.peaks.json labels.json --plans 80

The judge (eval/box_judge.py) gives every candidate window a probability. Sure ones (under 0.2 or over 0.8) are
decided by the judge. Each unsure one is shown to Opus: a zoomed crop, the windows the judge is sure of in blue, the
unsure candidate in red, and the question "is the red box one more separate window?". Compared on the same plans:
  A  the detector's own threshold (Aleks's engine)
  B  judge alone
  C  judge, Opus decides the unsure candidates
  D  judge, Opus's verdict used as one more piece of evidence (weight learned on other plans)
Opus replies are cached in rival/out/opus_unsure.json. Needs Pillow and ~/.config/bob/anthropic_key.
"""
import base64, io, json, os, sys, urllib.request, zlib
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from box_judge import candidates, logit  # noqa: E402
from calibrate import fit, predict  # noqa: E402

ARG = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
MODEL, LO, HI = ARG("--model", "claude-opus-5-5"), 0.2, 0.8
CACHE = os.path.join(ROOT, "rival", "out", "opus_unsure.json")


def ask(key, im, box, sure_boxes):
    x0, y0, x1, y1 = box
    m = max(x1 - x0, y1 - y0) * 1.8 + 40
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    crop = im.crop((int(cx - m), int(cy - m), int(cx + m), int(cy + m)))
    k = 640 / crop.width
    crop = crop.resize((640, 640), Image.LANCZOS)
    d = ImageDraw.Draw(crop)
    T = lambda b, pad: [(b[0] - cx + m) * k - pad, (b[1] - cy + m) * k - pad, (b[2] - cx + m) * k + pad, (b[3] - cy + m) * k + pad]
    for b in sure_boxes:
        d.rectangle(T(b, 2), outline=(0, 90, 255), width=2)
    d.rectangle(T(box, 5), outline=(255, 0, 0), width=3)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    text = ("Crop of an architectural floor plan. Blue boxes mark windows that are already counted. "
            "Is the red box one more separate window that should be added to the count? Answer no if it is not a window, "
            "if it is the same window as a blue box, or only a part of one. "
            'Reply with JSON only: {"add": true or false, "sure": a number from 0 to 1}')
    body = dict(model=MODEL, max_tokens=300, messages=[dict(role="user", content=[
        dict(type="image", source=dict(type="base64", media_type="image/png", data=base64.b64encode(buf.getvalue()).decode())),
        dict(type="text", text=text)])])
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=120))
    t = "".join(c.get("text", "") for c in r["content"] if c.get("type") == "text")
    v = json.loads(t[t.index("{"):t.rindex("}") + 1])
    return dict(add=bool(v["add"]), sure=float(v.get("sure", 0.5)), tin=r["usage"]["input_tokens"], tout=r["usage"]["output_tokens"])


def main():
    f_boxes, f_peaks, f_labels = sys.argv[1:4]
    n_plans = int(ARG("--plans", "80"))
    boxes = {json.loads(l)["path"][:-4]: json.loads(l)["boxes"] for l in open(f_boxes)}
    peaks, labels = json.load(open(f_peaks)), json.load(open(f_labels))
    Q = {os.path.basename(q["path"])[:-4]: q["answer"] for q in (json.loads(l) for l in open(os.path.join(HERE, "test_scans.jsonl")))
         if q["kind"] == "window" and q["lang"] == "en"}
    names = sorted(n for n in boxes if n in Q and n in peaks)
    data = {n: candidates([b for b in boxes[n] if b["cls"] == "window" and b["conf"] >= 0.1], peaks[n]["window"],
                          labels[n]["points"]["window"], labels[n]["w"], labels[n]["h"]) for n in names}
    fold = lambda n: zlib.crc32(n.encode()) % 5
    P = {}
    for f in range(5):
        w = fit([x for n in names if fold(n) != f for x, _, _ in data[n]], [t for n in names if fold(n) != f for _, t, _ in data[n]], steps=300)
        for n in names:
            if fold(n) == f:
                P[n] = [predict(w, x) for x, _, _ in data[n]]
    chosen = sorted(names, key=lambda n: zlib.crc32(("pick" + n).encode()))[:n_plans]
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    key = open(os.path.expanduser("~/.config/bob/anthropic_key")).read().strip()
    todo = [(n, i) for n in chosen for i, p in enumerate(P[n]) if LO < p < HI and f"{n}:{i}" not in cache]
    imgs = {}

    def one(job):
        n, i = job
        if n not in imgs:
            imgs[n] = Image.open(os.path.join(ROOT, "junction-dataset-preview", "data", "cubicasa5k", n + ".png")).convert("RGB")
        try:
            return f"{n}:{i}", ask(key, imgs[n], data[n][i][2], [data[n][j][2] for j, p in enumerate(P[n]) if p >= HI])
        except Exception as e:
            return f"{n}:{i}", dict(error=f"{type(e).__name__}: {e}"[:150])

    with ThreadPoolExecutor(4) as ex:
        for k, v in ex.map(one, todo):
            cache[k] = v
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(cache, open(CACHE, "w"))

    unsure = [(n, i) for n in chosen for i, p in enumerate(P[n]) if LO < p < HI and "add" in cache.get(f"{n}:{i}", {})]
    # D: Opus as evidence, weight learned on the other plans' unsure candidates
    PD = {}
    for f in range(5):
        tr = [(n, i) for n, i in unsure if fold(n) != f]
        if len(tr) < 20:
            continue
        feat = lambda n, i: [1.0, logit(P[n][i]), 1.0 if cache[f"{n}:{i}"]["add"] else 0.0, cache[f"{n}:{i}"]["sure"] * (1 if cache[f"{n}:{i}"]["add"] else -1)]
        w = fit([feat(n, i) for n, i in tr], [data[n][i][1] for n, i in tr], steps=300)
        for n, i in unsure:
            if fold(n) == f:
                PD[(n, i)] = predict(w, feat(n, i))
    res = dict(A=0, B=0, C=0, D=0)
    for n in chosen:
        true = Q[n]
        a = sum(1 for b in boxes[n] if b["cls"] == "window" and b["conf"] >= 0.375)
        b = sum(1 for p in P[n] if p >= 0.5)
        c = sum(1 for i, p in enumerate(P[n]) if (cache.get(f"{n}:{i}", {}).get("add", p >= 0.5) if LO < p < HI else p >= 0.5))
        d = sum(1 for i, p in enumerate(P[n]) if PD.get((n, i), p) >= 0.5)
        for k, v in zip("ABCD", (a, b, c, d)):
            res[k] += v == true
    N = len(chosen)
    op = [cache[f"{n}:{i}"] for n, i in unsure]
    print(f"{N} plans, {sum(len(P[n]) for n in chosen)} candidate windows, {len(unsure)} unsure ones sent to Opus")
    print(f"on the unsure candidates: judge right {sum((P[n][i] >= 0.5) == bool(data[n][i][1]) for n, i in unsure)}/{len(unsure)}, "
          f"Opus right {sum(cache[f'{n}:{i}']['add'] == bool(data[n][i][1]) for n, i in unsure)}/{len(unsure)}, Opus said add to {sum(o['add'] for o in op)}")
    print("window count exactly right, same plans:")
    for k, name in zip("ABCD", ("A detector threshold (Aleks's engine)", "B judge alone", "C judge + Opus decides the unsure ones", "D judge + Opus as evidence")):
        print(f"  {name:42s} {res[k]}/{N} = {100 * res[k] / N:.1f}%")
    print(f"Opus tokens: {sum(o['tin'] for o in op)} in, {sum(o['tout'] for o in op)} out ({len(op)} calls)")
    errs = [v["error"] for v in cache.values() if "error" in v]
    if errs:
        print(len(errs), "Opus errors, e.g.", errs[0])


if __name__ == "__main__":
    main()
