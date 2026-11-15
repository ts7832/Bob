"""Inspect any recognised object without a question: hover shows what it is, click zooms in and opens its card (2D and
3D), Esc closes it. Checks a scan, a Swiss CAD sheet and a BIM plan.
    python3 viz/tests/inspect_objects.py BASE_URL OUT_DIR"""
import os, sys
from playwright.sync_api import sync_playwright

base, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
ok, errs = True, []
def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu"])
    for stem in ("colorful-11397", "site1102_bldg2242_floor10281", "bsc-molio_molio-with-uris_plan-02-klder"):
        pg = b.new_page(viewport=dict(width=1600, height=900))
        pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        pg.goto(f"{base}/?d={stem}"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(3000)
        hit = None
        for y in range(160, 820, 18):
            for x in range(420, 1180, 18):
                pg.mouse.move(x, y)
                if pg.locator(".dv-hoverlabel").count():
                    hit = (x, y, pg.locator(".dv-hoverlabel").inner_text().replace("\n", " ")); break
            if hit: break
        check(f"{stem[:22]}: hover names an object ({hit[2] if hit else None})", hit)
        if not hit: pg.close(); continue
        pg.mouse.click(hit[0], hit[1]); pg.wait_for_timeout(2200)
        title = pg.locator(".dv-detail .dv-title").inner_text() if pg.locator(".dv-detail").count() else ""
        check(f"{stem[:22]}: click opens its card ({title})", title.startswith("What this is"))
        pg.screenshot(path=f"{out}/inspect_{stem[:12]}_2d.png")
        pg.locator(".dv-tools button", has_text="3D").click(); pg.wait_for_timeout(4500)
        check(f"{stem[:22]}: card stays in 3D", pg.locator(".dv-detail").count() == 1)
        pg.screenshot(path=f"{out}/inspect_{stem[:12]}_3d.png")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(600)
        check(f"{stem[:22]}: Esc closes it", pg.locator(".dv-detail").count() == 0)
        pg.close()
    b.close()
check(f"no page or console errors {errs[:2]}", not errs)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
