"""Scripted demo in the team's judge page (ui/index.html via tools/ui_server.py): the rehearsed questions, offline-safe
(DRAWQA_LLM=0 wording), with captions. Checks every answer; with --video it records the run (backup video, .webm).

    python3 viz/tests/demo.py http://localhost:8770 OUT_DIR [--video] [--slow 1.0]

Runs on the Mac's GPU (Metal) so 3D is smooth in the recording. Exit code 1 if an answer is missing or the page errors.
"""
import argparse, os, sys, time
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("base"); ap.add_argument("out")
ap.add_argument("--video", action="store_true")
ap.add_argument("--slow", type=float, default=1.0, help="multiply all pauses (presenter pace)")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
W, H = 1600, 900
ok, errs, shots = True, [], []

def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu"])
    ctx = b.new_context(viewport=dict(width=W, height=H), **(dict(record_video_dir=a.out, record_video_size=dict(width=W, height=H)) if a.video else {}))
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    wait = lambda s: pg.wait_for_timeout(int(s * 1000 * a.slow))

    def caption(text, sub=""):
        if shots: pg.screenshot(path=os.path.join(a.out, f"step{len(shots):02d}.png"))
        pg.evaluate("""([t, s]) => {
          let c = document.getElementById('demo-caption')
          if (!c) { c = document.createElement('div'); c.id = 'demo-caption'; document.body.appendChild(c)
            c.style.cssText = 'position:fixed;left:50%;bottom:64px;transform:translateX(-50%);z-index:99999;background:rgba(29,35,48,.92);color:#fff;font:600 17px/1.35 system-ui,-apple-system,sans-serif;padding:10px 18px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.25);max-width:860px;text-align:center;pointer-events:none' }
          c.innerHTML = t + (s ? '<div style="font-weight:400;font-size:14px;opacity:.85;margin-top:3px">' + s + '</div>' : '')
        }""", [text, sub])
        shots.append(text)

    def ask(q, settle=4.0):
        pg.fill("#q", q); pg.click("#askbtn"); wait(settle)
        pg.evaluate("() => document.activeElement && document.activeElement.blur()") # keys go to the view
        return pg.locator(".dv-badge").inner_text() if pg.locator(".dv-badge").count() else ""

    def tool(label):
        pg.locator(".dv-tools button", has_text=label).first.click()

    def open_item(tab, text, settle=4.5):
        pg.click(f"#tabs button[data-tab={tab}]"); wait(0.4)
        pg.locator(f"#tab-{tab} > div", has_text=text).first.click(); wait(settle)

    pg.goto(a.base); pg.wait_for_load_state("networkidle"); wait(1.5)
    caption("DrawQA: ask a drawing, see the answer on the drawing", "Every highlight sits on the original sheet, so it can be checked in seconds")
    wait(3)

    # 1. scan: doors (detected)
    open_item("samples", "Scanned plan")
    caption("1 · Scanned plan: how many doors?", "Found by a trained detector. Each door lights up as its drawn swing, numbered")
    t = ask("How many doors are there?")
    check("scan doors answered (12)", t.startswith("12"))
    wait(2.5)
    caption("Check each one: ← → steps through the evidence", "Zoom plus a crop of the untouched original")
    for _ in range(2): pg.keyboard.press("ArrowRight"); wait(1.8)
    pg.keyboard.press("Escape"); wait(1)
    caption("3D for orientation: each counted door is its own model", "Heights are illustrative, answers come from the drawing")
    tool("3D"); wait(5.5)
    tool("2D"); wait(2)

    # 2. Swiss CAD: WCs (measured), 3D model, clean view
    open_item("samples", "Swiss floor plan")
    caption("2 · CAD floor plan: how many toilets?", "Measured from exact geometry: the whole symbol lights up")
    t = ask("How many toilets (WC) are drawn on this floor plan?")
    check("Swiss WCs answered (3)", t.startswith("3"))
    wait(2)
    pg.keyboard.press("ArrowRight"); wait(2)
    caption("In 3D the counted toilet is the highlighted model", "Walls drop so the selected object is never hidden")
    tool("3D"); wait(6)
    tool("2D"); wait(1.5); pg.keyboard.press("Escape"); wait(1)
    caption("Clean view: the plan redrawn from what the system read", "Swipe against the original to see any difference")
    tool("Clean view"); wait(2.5)
    h = pg.locator(".dv-swipe-handle").bounding_box()
    if h:
        y = h["y"] + h["height"] / 2
        pg.mouse.move(h["x"] + h["width"] / 2, y); pg.mouse.down()
        for x in (0.3, 0.75, 0.5): pg.mouse.move(W * x * 0.75, y, steps=25); wait(0.8)
        pg.mouse.up()
    check("clean view shown", pg.locator(".dv-swipe").count() == 1)
    wait(1.5); tool("Clean view"); wait(1)

    # 3. honesty: an area on a scan with no printed scale (the challenge's own example of when to decline)
    open_item("samples", "Scanned plan")
    caption("3 · Honest when it can't answer: the floor area of a scan", "No scale is printed and rooms are only detector boxes, so it declines instead of guessing")
    t = ask("What is the total floor area?")
    check("scan floor area: cannot determine, with its reason", "Can't answer" in t and "scale" in t and "language model" not in t)
    wait(4)

    # 4. real project sheet: rotated PDF page with two drawings
    pg.click("#tabs button[data-tab=gold]"); wait(0.4)
    item = pg.locator("#tab-gold > div", has_text="A477")
    if item.count():
        item.first.click(); wait(9)
        caption("4 · A real project sheet: two unit plans on one page", "The view says which drawing each door comes from")
        t = ask("How many doors are there?", settle=6)
        check("A477 doors answered (12)", t.startswith("12"))
        wait(2.5)
        if pg.locator(".dv-views").count():
            pg.select_option(".dv-views", index=2); wait(3)
            pg.select_option(".dv-views", index=0); wait(1.5)
    else:
        check("A477 in the gold list", False)

    caption("DrawQA: correct answers you can verify on the drawing", "2D · 3D · clean view · every item checkable")
    wait(3)
    vid = pg.video.path() if a.video else None
    ctx.close(); b.close()

check(f"no page or console errors {errs[:2]}", not errs)
if vid:
    final = os.path.join(a.out, "drawqa-demo.webm")
    os.replace(vid, final)
    print("video", final, f"{os.path.getsize(final) / 1e6:.1f} MB")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
