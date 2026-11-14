"""Interaction checks: a pan drag keeps the selection, a plain click on empty sheet clears it.
    python3 viz/tests/interact.py BASE_URL"""
import sys
from playwright.sync_api import sync_playwright
base = sys.argv[1]
ok = True
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport=dict(width=1600, height=900))
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(f"{base}/?d=colorful-11397&q=0&sel=window4"); page.wait_for_load_state("networkidle"); page.wait_for_timeout(2500)
    has = lambda: page.locator(".dv-detail").count() > 0
    print("selected at start:", has()); ok &= has()
    page.mouse.move(300, 450); page.mouse.down(); page.mouse.move(420, 520, steps=8); page.mouse.up(); page.wait_for_timeout(800)
    print("after pan drag still selected:", has()); ok &= has()
    page.mouse.click(700, 250); page.wait_for_timeout(800)  # open sheet, away from the cards, no movement
    print("after plain click cleared:", not has()); ok &= not has()
    print("page errors:", len(errs)); ok &= not errs
    b.close()
print("PASS" if ok else "FAIL"); sys.exit(0 if ok else 1)
