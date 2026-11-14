"""Screenshot every fixture drawing in several states and collect console errors.

    python3 viz/tests/matrix.py BASE_URL OUT_DIR [--only STEM] [--size 1600x900]
Per drawing: plain sheet, first question, first question with its first evidence selected, last probe (ceiling height,
always "cannot determine"), plus any question whose evidence is guids. Exit code 1 if any page logged an error.
"""
import argparse, json, os, sys
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("base"); ap.add_argument("out")
ap.add_argument("--only"); ap.add_argument("--size", default="1600x900")
ap.add_argument("--live", action="store_true")
a = ap.parse_args()
w, h = map(int, a.size.split("x"))
FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")
os.makedirs(a.out, exist_ok=True)

cases = []
for f in json.load(open(os.path.join(FX, "index.json"))):
    s = f["stem"]
    if a.only and a.only not in s:
        continue
    asks = json.load(open(os.path.join(FX, s, "ask.json")))
    first_ev = next(((i, e) for i, q in enumerate(asks) for e in (q["response"].get("evidence") or [])[:1]), None)
    cases.append((f"{s[:24]}_0plain", dict(d=s)))
    cases.append((f"{s[:24]}_1q0", dict(d=s, q=0)))
    if first_ev:
        i, e = first_ev
        cases.append((f"{s[:24]}_2sel", dict(d=s, q=i, sel=e.get("id"))))
    cases.append((f"{s[:24]}_3cannot", dict(d=s, q=len(asks) - 1)))

bad = 0
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    for name, q in cases:
        if a.live:
            q = dict(q, live=1)
        page = b.new_page(viewport=dict(width=w, height=h))
        errs = []
        page.on("console", lambda m: m.type == "error" and errs.append(m.text))
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        page.goto(f"{a.base}/?{urlencode(q)}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1800)
        out = os.path.join(a.out, name + ".png")
        page.screenshot(path=out)
        info = page.evaluate("""() => ({
            badge: document.querySelector('.dv-badge')?.innerText?.replace(/\\s+/g, ' ').slice(0, 140) ?? null,
            tags: document.querySelectorAll('.dv-tag').length,
            detail: !!document.querySelector('.dv-detail'),
            status: document.querySelector('.dv-status')?.innerText ?? null })""")
        bad += bool(errs)
        print(f"{name:34} errors={len(errs)} tags={info['tags']:3} detail={int(info['detail'])} badge={info['badge']} status={info['status']}")
        for e in errs[:3]:
            print("    ", e[:240])
        page.close()
    b.close()
sys.exit(1 if bad else 0)
