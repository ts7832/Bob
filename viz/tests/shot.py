"""Screenshot a viz page and report console errors and visible label tags.

    python3 viz/tests/shot.py URL OUT.png [--wait-ms 2500] [--size 1600x900]
Exit code 1 when the page logged errors.
"""
import argparse, sys
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("url"); ap.add_argument("out")
ap.add_argument("--wait-ms", type=int, default=2500)
ap.add_argument("--size", default="1600x900")
a = ap.parse_args()
w, h = map(int, a.size.split("x"))

errors, logs = [], []
with sync_playwright() as p:
    # swiftshader gives headless Chrome a working WebGL context
    b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport=dict(width=w, height=h))
    page.on("console", lambda m: (errors if m.type == "error" else logs).append(f"{m.type}: {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(a.url)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(a.wait_ms)
    page.screenshot(path=a.out)
    tags = page.locator(".dv-tag").count()
    canvas = page.locator("canvas").count()
    b.close()

print(f"shot {a.out}  canvas={canvas} tags={tags} errors={len(errors)} warnings={sum(l.startswith('warning') for l in logs)}")
for e in errors[:10]:
    print("  ", e[:300])
sys.exit(1 if errors else 0)
