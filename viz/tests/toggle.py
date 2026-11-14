"""Click 3D then 2D in the harness and capture frames of both transitions; compares the end state with the start.
    python3 viz/tests/toggle.py URL OUT_DIR"""
import sys
from playwright.sync_api import sync_playwright
from PIL import Image, ImageChops

url, out = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport=dict(width=1600, height=900))
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(url); page.wait_for_load_state("networkidle"); page.wait_for_timeout(2500)
    page.screenshot(path=f"{out}/t0_2d.png")
    page.get_by_role("button", name="3D").click()
    for ms in (250, 600, 2500):
        page.wait_for_timeout(ms - (0 if ms == 250 else {600: 250, 2500: 600}[ms]))
        page.screenshot(path=f"{out}/t1_3d_{ms}.png")
    page.get_by_role("button", name="2D").click()
    page.wait_for_timeout(3000)
    page.screenshot(path=f"{out}/t2_back.png")
    b.close()
a, c = Image.open(f"{out}/t0_2d.png").convert("RGB"), Image.open(f"{out}/t2_back.png").convert("RGB")
box = (0, 0, 1200, 900)  # the view only, not the harness panel
diff = ImageChops.difference(a.crop(box), c.crop(box))
bbox = diff.getbbox()
px = sum(1 for v in diff.convert("L").getdata() if v > 24)
print("errors", len(errs), "| changed px after round trip:", px, "of", 1200 * 900, "bbox", bbox)
