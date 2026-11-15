"""Judge-eye QA tour inside the team page (ui/): for every sample (and A477 from the checked set), ask the first listed
question, then screenshot 2D answer, first item selected, 3D, Photo and the clean view. One contact sheet per drawing,
for a person to look at. GPU (Metal) headless, so 3D and Photo look as on a laptop.

    python3 viz/tests/qa_tour.py http://localhost:8770 OUT_DIR [--only NAME]
"""
import argparse, os, sys
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw

ap = argparse.ArgumentParser()
ap.add_argument("base"); ap.add_argument("out"); ap.add_argument("--only")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
errs = []

def sheet(name, shots):
    ims = [Image.open(p).resize((800, 450)) for _, p in shots]
    o = Image.new("RGB", (1610, 460 * ((len(ims) + 1) // 2)), "#888")
    d = ImageDraw.Draw(o)
    for k, (label, _) in enumerate(shots):
        x, y = (k % 2) * 810, (k // 2) * 460
        o.paste(ims[k], (x, y)); d.rectangle([x, y, x + 160, y + 22], fill="#1d2330"); d.text((x + 6, y + 5), label, fill="#fff")
    path = os.path.join(a.out, f"tour_{name}.png"); o.save(path); print("sheet", path)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu"])
    pg = b.new_page(viewport=dict(width=1600, height=900))
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.goto(a.base); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1200)
    pg.click("#tabs button[data-tab=samples]"); pg.wait_for_timeout(300)
    n = pg.locator("#tab-samples > div").count()
    targets = [("samples", i) for i in range(n)] + [("gold", "A477")]
    for tab, key in targets:
        pg.click(f"#tabs button[data-tab={tab}]"); pg.wait_for_timeout(300)
        item = pg.locator(f"#tab-{tab} > div").nth(key) if isinstance(key, int) else pg.locator(f"#tab-{tab} > div", has_text=key).first
        title = item.inner_text().split("\n")[0][:40]
        if a.only and a.only.lower() not in title.lower():
            continue
        name = "".join(c if c.isalnum() else "_" for c in title)[:30]
        item.click(); pg.wait_for_timeout(5000)
        shots = []
        def snap(label):
            path = os.path.join(a.out, f"{name}_{len(shots)}.png"); pg.screenshot(path=path); shots.append((label, path))
        chips = pg.locator("#chips button, #chips .chip")
        if chips.count():
            chips.first.click(); pg.wait_for_timeout(400)
            if pg.locator("#answersec").is_hidden(): pg.click("#askbtn")
            pg.wait_for_timeout(4500)
        snap("2D answer")
        ev = pg.locator(".evlist button")
        if ev.count(): ev.first.click(); pg.wait_for_timeout(1800); snap("item selected")
        btn3d = pg.locator(".dv-tools button:not([disabled])", has_text="3D")
        if btn3d.count():
            btn3d.first.click(); pg.wait_for_timeout(5000); snap("3D")
            photo = pg.locator(".dv-tools button", has_text="Photo")
            if photo.count(): photo.first.click(); pg.wait_for_timeout(9000); snap("Photo"); photo.first.click(); pg.wait_for_timeout(500)
            pg.locator(".dv-tools button", has_text="2D").first.click(); pg.wait_for_timeout(2500)
        cv = pg.locator(".dv-tools button", has_text="Clean view")
        if cv.count(): cv.first.click(); pg.wait_for_timeout(3000); snap("clean view"); cv.first.click(); pg.wait_for_timeout(500)
        sheet(name, shots)
    b.close()
print("page errors:", len(errs), errs[:3])
sys.exit(1 if errs else 0)
