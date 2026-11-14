"""Our bundle inside the team's judge page (ui/index.html via tools/ui_server.py): every sample + a gold drawing, a
rehearsed question each, evidence list -> view selection, 3D, Space on the page's own button, console errors.
    python3 viz/tests/teampage.py http://localhost:8770 OUT_DIR"""
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
    errs = []
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.goto(base); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
    check("view mounted in the team page", pg.locator(".dv-root").count() == 1)
    pg.click("#tabs button[data-tab=samples]"); pg.wait_for_timeout(500)
    items = pg.locator("#tab-samples > div")
    n = items.count(); print(f"     {n} samples")
    for i in range(n):
        items.nth(i).click(); pg.wait_for_timeout(3500)
        title = items.nth(i).inner_text().split("\n")[0][:40]
        chips = pg.locator("#chips button, #chips .chip")
        if chips.count() == 0:
            check(f"sample {i} ({title}): has rehearsed questions", False); continue
        chips.first.click(); pg.wait_for_timeout(500)
        if pg.locator("#answersec").is_hidden(): pg.click("#askbtn")
        pg.wait_for_timeout(3500)
        badge = pg.locator(".dv-badge")
        check(f"sample {i} ({title}): answer shown in the view", badge.count() == 1)
        ev = pg.locator(".evlist button")
        if ev.count():
            ev.first.click(); pg.wait_for_timeout(1200)
            check(f"sample {i}: evidence list click -> view selects it (or says it is not on this sheet)", pg.locator(".dv-detail").count() == 1)
            if pg.locator(".dv-offsheet").count(): print("      (off-sheet element explained)")
            ev.first.focus(); pg.keyboard.press(" "); pg.wait_for_timeout(300)
            check(f"sample {i}: Space on the page's own button does not trigger our peek", pg.locator(".dv-peek").count() == 0)
        pg.screenshot(path=f"{out}/team_sample{i}.png")
    pg.locator(".dv-tools button", has_text="3D").click(); pg.wait_for_timeout(3500)
    check("3D in the team page", pg.locator(".dv-illustrative").count() == 1)
    pg.screenshot(path=f"{out}/team_3d.png")
    pg.click("#tabs button[data-tab=gold]"); pg.wait_for_timeout(500)
    g = pg.locator("#tab-gold > div")
    if g.count():
        g.first.click(); pg.wait_for_timeout(4000)
        chips = pg.locator("#chips button, #chips .chip")
        if chips.count(): chips.first.click(); pg.wait_for_timeout(500); pg.click("#askbtn"); pg.wait_for_timeout(4000)
        check("gold drawing + question shows in the view", pg.locator(".dv-badge").count() == 1 or pg.locator(".dv-status").count() == 1)
        pg.screenshot(path=f"{out}/team_gold.png")
    check("no page or console errors", not errs)
    for e in errs[:6]: print("     ", e[:220])
    b.close()
print("ALL PASS" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
