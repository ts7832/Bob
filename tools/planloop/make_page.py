"""gallery_data.json + JPEGs -> one self-contained HTML page (images embedded)."""
import base64, html, json, os

P = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(P, "gallery")
d = json.load(open(os.path.join(G, "gallery_data.json")))
img = lambda f: "data:image/jpeg;base64," + base64.b64encode(open(os.path.join(G, f), "rb").read()).decode()
e = html.escape
short = lambda p: p.replace("high_quality_architectural-", "arch-").replace("high_quality-", "hq-")


def panel(src, title, meta=""):
    return (f'<figure class="panel"><button class="zoom" type="button" aria-label="Enlarge {e(title)}"><img src="{img(src)}" alt="{e(title)}" loading="lazy"></button>'
            f'<figcaption><span class="ptitle">{e(title)}</span>{meta}</figcaption></figure>')


def chg(a, b, label):
    return f'<li><span class="k">{label}</span><span class="v">{a}{" → " + str(b) if a != b else ""}</span></li>'


rev = []
for r in d["review"]:
    s, f = r["start"], r["final"]
    delta = r["claude_ok"] - r["det_ok"]
    tag = "up" if delta > 0 else "down" if delta < 0 else "same"
    qs = "".join(f'<li class="{"gain" if q["ok"] else "loss"}"><b>{"Gained" if q["ok"] else "Lost"}</b> {e(q["q"].split("?")[0][:70])}? '
                 f'<span class="mono">answer {e(q["key"])} · detector {e(q["detector"])} · reviewed {e(q["model"])}</span></li>' for q in r["changed"])
    rev.append(f'''<article class="case">
  <header class="casehead"><h3 class="mono">{e(short(r["plan"]))}</h3>
    <span class="score {tag}">{r["det_ok"]}/{r["n"]} → {r["claude_ok"]}/{r["n"]}</span>
    <span class="cost mono">{r["s"]} s · {round(r["tokens"] / 1000)}k tokens</span></header>
  <div class="row3">{panel(r["input"], "Input scan")}{panel(r["detector"], "Detector's sheet")}{panel(r["claude"], "After Claude's review")}</div>
  <div class="facts"><ul class="counts">{chg(s["rooms"], f["rooms"], "rooms")}{chg(s["typed_rooms"], f["typed_rooms"], "typed rooms")}{chg(s["doors"], f["doors"], "doors")}{chg(s["windows"], f["windows"], "windows")}{chg(s["fixtures"], f["fixtures"], "fixtures")}</ul>
  {f'<ul class="qs">{qs}</ul>' if qs else '<p class="quiet">No answer changed.</p>'}</div>
</article>''')

scr = [panel(d["scratch_input"], "Input scan", '<span class="pmeta">detector\'s sheet: 7/9</span>')]
for s in d["scratch"]:
    scr.append(panel(s["img"], s["label"], f'<span class="pmeta"><b>{s["ok"]}/{s["n"]}</b> · {s["s"]} s · {round(s["tokens"] / 1000)}k tokens</span>'))

p = d["p1000_scratch"]
p1000 = "".join([panel(d["p1000_input"], "Input scan"),
                 panel(d["p1000_detector"], "Detector's sheet", f'<span class="pmeta"><b>{p["det_ok"]}/{p["n"]}</b> · windows {p["detector_counts"]["windows"]}</span>'),
                 panel(p["img"], "Opus · default effort, from scratch", f'<span class="pmeta"><b>{p["ok"]}/{p["n"]}</b> · {round(p["s"] / 60)} min · {round(p["tokens"] / 1000)}k tokens · windows {p["counts"]["windows"]}</span>')])

R = d["review"]
tot_n, tot_d, tot_c = sum(r["n"] for r in R), sum(r["det_ok"] for r in R), sum(r["claude_ok"] for r in R)

page = open(os.path.join(P, "page_template.html")).read()
for k, v in {"%%REVIEW%%": "\n".join(rev), "%%SCRATCH%%": "".join(scr), "%%P1000%%": p1000, "%%TOT_N%%": str(tot_n), "%%TOT_D%%": str(tot_d),
             "%%TOT_C%%": str(tot_c), "%%SCALE1000%%": e(p["scale"] or "")}.items():
    page = page.replace(k, v)
open(os.path.join(P, "plan_rebuilds.html"), "w").write(page)
print(round(len(page) / 1e6, 2), "MB")
