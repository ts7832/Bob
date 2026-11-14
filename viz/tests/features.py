"""Feature checks for the review/verification tools: evidence stepper, peek original, measure, double-click zoom,
legend group toggle. Screenshots go to OUT_DIR.   python3 viz/tests/features.py BASE_URL OUT_DIR"""
import sys
from playwright.sync_api import sync_playwright
base, out = sys.argv[1], sys.argv[2]
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    pg = b.new_page(viewport=dict(width=1600, height=900))
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    load = lambda q: (pg.goto(f"{base}/?{q}"), pg.wait_for_load_state("networkidle"), pg.wait_for_timeout(2500))
    tags = lambda: pg.locator(".dv-tag").count()

    load("d=colorful-11397&q=0")                       # 13 windows
    pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(700)
    check("stepper: first item selected (1 of 13)", "1 of 13" in pg.locator(".dv-step span").inner_text() and pg.locator(".dv-detail").count() == 1)
    pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(700)
    check("stepper: next item (2 of 13)", "2 of 13" in pg.locator(".dv-step span").inner_text())
    pg.screenshot(path=f"{out}/step.png")
    n = tags(); pg.keyboard.down(" "); pg.wait_for_timeout(500)
    check("peek: highlights hidden while Space is held", tags() == 0 and pg.locator(".dv-peek").count() == 1)
    pg.screenshot(path=f"{out}/peek.png")
    pg.keyboard.up(" "); pg.wait_for_timeout(500)
    check("peek: highlights back after release", tags() == n)

    load("d=site1102_bldg2242_floor10281")               # Swiss, metres
    pg.keyboard.press("m"); pg.wait_for_timeout(300)
    for x, y in [(560, 300), (760, 300), (760, 520), (560, 520), (561, 301)]:
        pg.mouse.click(x, y); pg.wait_for_timeout(250)
    txt = pg.locator(".dv-measure").inner_text()
    check("measure: closed area in m² with its scale source", "m² area" in txt and "metres" in txt)
    print("     readout:", txt.replace("\n", " | "))
    pg.screenshot(path=f"{out}/measure.png")
    pg.keyboard.press("Escape"); pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    check("measure: Esc clears and exits", pg.locator(".dv-measure").count() == 0)

    before = pg.screenshot(); pg.mouse.dblclick(700, 420); pg.wait_for_timeout(1500)
    check("double-click zooms in (view changed)", pg.screenshot() != before)
    pg.screenshot(path=f"{out}/dblzoom.png")

    load("d=site11692_bldg18240_floor48820&synthetic=areas")
    pg.wait_for_timeout(800)
    rows = pg.locator(".dv-legend li"); rows.nth(4).click(); pg.wait_for_timeout(700)  # "Office"
    check("legend: group switched off", "dv-off" in (rows.nth(4).get_attribute("class") or ""))
    pg.screenshot(path=f"{out}/legend.png")
    check("no page errors", not errs)
    if errs: print(errs[:3])
    b.close()
print("ALL PASS" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
