"""Cold-start check for issue #31: start tools/ui_server.py fresh on a spare port, open a BIM plan FIRST in ui/ (the
page loads several /api endpoints in parallel), and report whether the server survives.

    python3 viz/tests/coldstart.py [--bob ~/junction2026/Bob] [--port 8771]

Exit code 1 if the server dies (the ifcopenshell race in drawqa/ifc.py) or the page cannot open the plan.
"""
import argparse, os, subprocess, sys, time, urllib.request
from playwright.sync_api import sync_playwright

here = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--bob", default=os.path.expanduser("~/junction2026/Bob"))
ap.add_argument("--port", type=int, default=8771)
a = ap.parse_args()
viz = os.path.join(here, "..", "dist", "drawview.js")
env = dict(os.environ, PYTHONFAULTHANDLER="1")
srv = subprocess.Popen([os.path.join(a.bob, ".venv", "bin", "python"), "-u", "tools/ui_server.py", "--port", str(a.port), "--viz", viz],
                       cwd=a.bob, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
base = f"http://localhost:{a.port}"
for _ in range(60):
    try:
        urllib.request.urlopen(base, timeout=1); break
    except Exception:
        time.sleep(0.5)
ok = True
try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--use-angle=metal", "--enable-gpu"])
        pg = b.new_page(viewport=dict(width=1600, height=900))
        pg.goto(base); pg.wait_for_load_state("networkidle")
        pg.click("#tabs button[data-tab=samples]"); pg.wait_for_timeout(300)
        for name in ("Molio", "Esplanades"):
            pg.locator("#tab-samples > div", has_text=name).first.click(); pg.wait_for_timeout(7000)
            alive = srv.poll() is None
            print(("PASS " if alive else "FAIL ") + f"server alive after opening {name} first on a cold cache")
            ok &= alive
            if not alive: break
        if ok:
            shown = pg.locator(".dv-root canvas").count() > 0 and pg.locator(".dv-status").count() == 0
            print(("PASS " if shown else "FAIL ") + "the BIM plan is drawn in the view"); ok &= shown
        b.close()
finally:
    if srv.poll() is None:
        srv.terminate()
    out = srv.communicate(timeout=10)[0]
    if not ok:
        tail = [l for l in out.splitlines() if l.strip()][-14:]
        print("     server output:\n      " + "\n      ".join(tail))
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
