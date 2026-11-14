"""Render input scans and every model (detector, Claude review, Claude from scratch) as images, score each through our
engine, and write gallery_data.json + JPEGs. Run from the repo root with the project venv."""
import base64, io, json, os, re, subprocess, sys
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

P = os.path.dirname(os.path.abspath(__file__))
IMG = "junction-dataset-preview/data/cubicasa5k/{}.png"
OUT = os.path.join(P, "gallery")
os.makedirs(OUT, exist_ok=True)
SIDE = 860
ROOM = {"bedroom": (59, 130, 246), "living_room": (16, 185, 129), "kitchen": (245, 158, 11), "dining": (234, 179, 8),
        "bathroom": (6, 182, 212), "wc_room": (139, 92, 246), "sauna_room": (239, 68, 68), "utility": (132, 204, 22),
        "storage": (120, 113, 108), "dressing_room": (168, 162, 158), "entry": (236, 72, 153), "balcony": (34, 197, 94),
        "terrace": (21, 128, 61), "garage": (100, 116, 139), "room": (148, 163, 184), "other_room": (148, 163, 184)}
FIX_TAG = {"wc": "WC", "sink": "S", "shower": "SH", "bathtub": "BT", "sauna_stove": "ST", "sauna_bench": "B"}


def font(n):
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(f, n)
        except Exception:
            pass
    return ImageFont.load_default()


def base(plan, faded):
    im = Image.open(IMG.format(plan)).convert("RGB")
    k = SIDE / max(im.size)
    im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    if faded:
        im = Image.blend(Image.new("RGB", im.size, (255, 255, 255)), im, 0.28)
    return im, k


def render(plan, model, name):
    im, k = base(plan, True)
    d = ImageDraw.Draw(im, "RGBA")
    f, fs = font(13), font(11)
    S = lambda b: [b[0] * k, b[1] * k, b[2] * k, b[3] * k]
    for fl in model.get("floors", []) if isinstance(model.get("floors"), list) else []:
        d.rectangle(S(fl["bbox"]), outline=(30, 41, 59, 160), width=2)
    for r in model.get("rooms", []):
        pts = [(x * k, y * k) for x, y in r.get("polygon", [])]
        if len(pts) < 3:
            continue
        c = ROOM.get(r.get("type"), ROOM["other_room"])
        d.polygon(pts, fill=c + (70,), outline=c + (255,))
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        label = (r.get("name") or "").strip() or r.get("type", "").replace("_", " ")
        d.text((cx, cy), label[:18], fill=(15, 23, 42, 255), font=f, anchor="mm", stroke_width=2, stroke_fill=(255, 255, 255, 230))
    for kind, col in (("doors", (37, 99, 235)), ("windows", (13, 148, 136))):
        for e in model.get(kind, []):
            d.rectangle(S(e["bbox"]), outline=col + (255,), width=3)
    for e in model.get("fixtures", []):
        b = S(e["bbox"])
        d.rectangle(b, outline=(147, 51, 234, 255), width=3)
        d.text((b[0] + 2, b[1] + 1), FIX_TAG.get(e.get("type"), "?"), fill=(147, 51, 234, 255), font=fs, stroke_width=2, stroke_fill=(255, 255, 255, 230))
    path = os.path.join(OUT, name + ".jpg")
    im.save(path, quality=78)
    return name + ".jpg"


def render_input(plan):
    im, _ = base(plan, False)
    im.save(os.path.join(OUT, plan + "_input.jpg"), quality=80)
    return plan + "_input.jpg"


def counts(m):
    fx = {}
    for e in m.get("fixtures", []):
        fx[e.get("type")] = fx.get(e.get("type"), 0) + 1
    typed = sum(1 for r in m.get("rooms", []) if r.get("type") not in (None, "room", "other_room"))
    return dict(doors=len(m.get("doors", [])), windows=len(m.get("windows", [])), fixtures=sum(fx.values()), rooms=len(m.get("rooms", [])), typed_rooms=typed)


def score(plan, model_path, key, engine):
    out = subprocess.run([sys.executable, f"{P}/engine_on_model.py", IMG.format(plan), model_path, key, engine], capture_output=True, text=True,
                         env=dict(os.environ, DRAWQA_OUTLINES="0")).stdout
    rows = []
    for l in out.splitlines():
        m = re.match(r"(OK |X  ) key=(\S+)\s+frontier-sheet=(\S+)\s+detector=(\S+)\s+(.*)", l)
        if m:
            rows.append(dict(ok=m.group(1) == "OK ", key=m.group(2), model=m.group(3), detector=m.group(4), q=m.group(5)))
    return rows


data = dict(review=[], scratch=[], scratch1000=[])
T2 = {"high_quality_architectural-6581": (41, 50055), "high_quality-8530": (46, 53216), "high_quality_architectural-11826": (50, 52789),
      "high_quality_architectural-8639": (50, 58490), "high_quality_architectural-7041": (52, 54445), "high_quality_architectural-14339": (53, 59137),
      "high_quality_architectural-7598": (55, 63434), "high_quality_architectural-9173": (56, 69208), "high_quality-729": (57, 55623),
      "high_quality_architectural-41": (57, 58895)}
for plan in sorted(os.listdir(f"{P}/fix2")):
    d = f"{P}/fix2/{plan}"
    key, eng = f"{P}/fixkeys2/{plan}/KEY.json", f"{P}/fixkeys2/{plan}/ENGINE.json"
    start, final = json.load(open(f"{d}/start_model.json")), json.load(open(f"{d}/model_final.json"))
    rows = score(plan, f"{d}/model_final.json", key, eng)
    data["review"].append(dict(plan=plan, input=render_input(plan), detector=render(plan, start, plan + "_detector"),
                               claude=render(plan, final, plan + "_claude"), start=counts(start), final=counts(final),
                               det_ok=sum(r["key"] == r["detector"] for r in rows), claude_ok=sum(r["ok"] for r in rows), n=len(rows),
                               changed=[r for r in rows if r["ok"] != (r["key"] == r["detector"])],
                               s=T2.get(plan, (None, None))[0], tokens=T2.get(plan, (None, None))[1]))
    print("review", plan, flush=True)

timings = json.load(open(f"{P}/timings.json"))
p6 = "high_quality_architectural-6001"
key6, eng6 = f"{P}/high_quality_architectural-6001.KEY.json", f"{P}/high_quality_architectural-6001.ENGINE.json"
data["scratch_input"] = render_input(p6)
for run, label in (("v2_opus_6001", "Opus · default effort"), ("v2_opus-medium_6001", "Opus · medium"), ("v2_opus-low_6001", "Opus · low"),
                   ("v2_sonnet-medium_6001", "Sonnet · medium"), ("v2_sonnet-low_6001", "Sonnet · low"),
                   ("v2_haiku-medium_6001", "Haiku · medium"), ("v2_haiku-low_6001", "Haiku · low")):
    mp = f"{P}/{run}/model_final.json"
    if not os.path.exists(mp):
        continue
    m = json.load(open(mp))
    if not isinstance(m.get("floors"), list):
        m["floors"] = []
    rows = score(p6, mp, key6, eng6)
    t = timings.get(run, {})
    data["scratch"].append(dict(run=run, label=label, img=render(p6, m, run), counts=counts(m), ok=sum(r["ok"] for r in rows), n=len(rows),
                                det_ok=sum(r["key"] == r["detector"] for r in rows), s=t.get("s"), tokens=t.get("tokens"),
                                scale=(m.get("scale_source") or "")[:120]))
    print("scratch", run, flush=True)

p1 = "high_quality_architectural-1000"
key1, eng1 = f"{P}/high_quality_architectural-1000.KEY.json", f"{P}/high_quality_architectural-1000.ENGINE.json"
data["p1000_input"] = render_input(p1)
start1 = json.load(open(f"{P}/fix/{p1}/start_model.json"))
data["p1000_detector"] = render(p1, start1, p1 + "_detector")
m1 = json.load(open(f"{P}/v2_opus_1000/model_final.json"))
rows1 = score(p1, f"{P}/v2_opus_1000/model_final.json", key1, eng1)
data["p1000_scratch"] = dict(img=render(p1, m1, "v2_opus_1000"), counts=counts(m1), ok=sum(r["ok"] for r in rows1), n=len(rows1),
                             det_ok=sum(r["key"] == r["detector"] for r in rows1), s=timings["v2_opus_1000"]["s"],
                             tokens=timings["v2_opus_1000"]["tokens"], scale=m1.get("scale_source"), detector_counts=counts(start1), rows=rows1)
json.dump(data, open(f"{OUT}/gallery_data.json", "w"), indent=1)
print("done", sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT)) // 1024, "KB of images")
