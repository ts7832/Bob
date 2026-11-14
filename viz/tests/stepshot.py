"""Open a harness URL, step to the n-th evidence item with the arrow key, screenshot.

    python3 viz/tests/stepshot.py URL OUT.png [--n 1] [--wait-ms 3000]
"""
import argparse
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("url"); ap.add_argument("out")
ap.add_argument("--n", type=int, default=1); ap.add_argument("--wait-ms", type=int, default=3000)
a = ap.parse_args()
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport=dict(width=1600, height=900))
    page.on("console", lambda m: m.type == "error" and errs.append(m.text))
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(a.url); page.wait_for_load_state("networkidle"); page.wait_for_timeout(a.wait_ms)
    for _ in range(a.n):
        page.keyboard.press("ArrowRight"); page.wait_for_timeout(300)
    page.wait_for_timeout(1500)
    page.screenshot(path=a.out)
    b.close()
print("stepshot", a.out, "errors", len(errs), errs[:3])
