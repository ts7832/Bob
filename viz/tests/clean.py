"""Clean view checks: button, swipe divider (drag + keys), C key, hidden in 3D and on sheets without detections,
answers still drawn on top, left side redrawn / right side original. Screenshots go to OUT_DIR.
    python3 viz/tests/clean.py BASE_URL OUT_DIR"""
import os, sys
from playwright.sync_api import sync_playwright
from PIL import Image, ImageChops

base, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
ok = True
def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)

def diff(a, b, box):
    d = ImageChops.difference(Image.open(a).convert("RGB").crop(box), Image.open(b).convert("RGB").crop(box))
    return sum(1 for p in d.getdata() if max(p) > 24)

errs = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    pg = b.new_page(viewport=dict(width=1600, height=900))
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(f"{base}/?d=site1102_bldg2242_floor10281"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(2500)
    pg.screenshot(path=f"{out}/0_original.png")
    btn = pg.locator(".dv-tools button", has_text="Clean view")
    check("Clean view button on a Swiss sheet", btn.count() == 1)
    btn.click(); pg.wait_for_timeout(2000)
    check("divider shown", pg.locator(".dv-swipe").count() == 1)
    pg.screenshot(path=f"{out}/1_clean.png")
    view = pg.locator(".dv-view").bounding_box()
    mid = int(view["x"] + view["width"] / 2)
    left = diff(f"{out}/0_original.png", f"{out}/1_clean.png", (int(view["x"]) + 300, 150, mid - 20, 800))
    right = diff(f"{out}/0_original.png", f"{out}/1_clean.png", (mid + 20, 150, int(view["x"] + view["width"]) - 20, 700))
    print(f"     changed px left {left}, right {right}")
    check("left of the divider is redrawn", left > 5000)
    check("right of the divider is the untouched original", right < 300)
    key = pg.evaluate("() => [...document.querySelectorAll('.dv-swipe-key span')].map(e => e.title)")  # icons, names on hover
    check(f"legend lists the symbols {key}", "doors" in key and "windows" in key)

    h = pg.locator(".dv-swipe-handle").bounding_box()
    pg.mouse.move(h["x"] + h["width"] / 2, h["y"] + h["height"] / 2); pg.mouse.down()
    pg.mouse.move(view["x"] + view["width"] * 0.3, h["y"] + 10, steps=8); pg.mouse.up(); pg.wait_for_timeout(500)
    pos = pg.evaluate("() => parseFloat(document.querySelector('.dv-swipe').style.left)")
    check(f"drag moves the divider (now {pos:.0f}%)", 27 < pos < 33)
    pg.screenshot(path=f"{out}/2_dragged.png")
    pg.locator(".dv-swipe-handle").focus(); pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(200)
    pos2 = pg.evaluate("() => parseFloat(document.querySelector('.dv-swipe').style.left)")
    check(f"arrow key on the handle moves it ({pos2:.0f}%)", pos2 > pos + 3)
    pg.evaluate("() => document.activeElement.blur()")
    pg.keyboard.press("c"); pg.wait_for_timeout(500)
    check("C switches it off", pg.locator(".dv-swipe").count() == 0)
    pg.keyboard.press("c"); pg.wait_for_timeout(800)
    check("C switches it on again", pg.locator(".dv-swipe").count() == 1)

    pg.goto(f"{base}/?d=site1102_bldg2242_floor10281&q=4&clean=0.5"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(3500)
    n = pg.locator(".dv-tag").count()  # overlapping tags are thinned out on dense answers (declutter)
    check(f"answer drawn over the clean view (21 doors, {n} tags readable)", pg.locator(".dv-badge").inner_text().startswith("21") and 8 <= n <= 21)
    pg.screenshot(path=f"{out}/3_answer.png")
    pg.locator(".dv-tools button", has_text="3D").click(); pg.wait_for_timeout(1500)
    check("3D: no divider, no Clean view button", pg.locator(".dv-swipe").count() == 0 and pg.locator(".dv-tools button", has_text="Clean view").count() == 0)

    for stem in ("colorful-11397", "bsc-molio_molio-with-uris_plan-02-klder"):
        pg.goto(f"{base}/?d={stem}&clean=0.55"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(3500)
        check(f"{stem[:20]}: divider shown", pg.locator(".dv-swipe").count() == 1)
        pg.screenshot(path=f"{out}/4_{stem[:12]}.png")

    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "judge", "school.png")):
        pg.goto(f"{base}/?img=/judge/school.png"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(12000)
        check("sheet without detections: no Clean view button", pg.locator(".dv-tools button", has_text="Clean view").count() == 0)
    b.close()
check(f"no page or console errors {errs[:2]}", not errs)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
