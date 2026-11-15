// Live reading on the blueprint window: Aleks's reading (demo/server.py /api/v2/*) shown as it improves. Our models'
// reading appears in seconds; then each time Claude (Opus, via Claude Code on this laptop) saves a better partial
// result, the new version is revealed with a pixel wipe from top to bottom. Every version is kept: click the strip to go back.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const vwin = document.querySelector(".vwin"), vtb = document.querySelector(".vwin > .tb");
  if (!vwin || !vtb) return;
  const tools = $("vtools") || vtb;
  tools.insertAdjacentHTML("afterbegin", '<button id="vlive" title="Watch the reading improve step by step: our models first, then Opus">▶ Live reading</button>');

  let job = null, timer = 0, versions = [], shown = -1, pic = null, picScale = 1, wiping = false, lastPartial = -1, lastView = -1, st = null;

  function panel() {
    if ($("livep")) return;
    vwin.insertAdjacentHTML("beforeend", `<div id="livep"><div class="lhead"><b id="lstage">Starting…</b><span id="lstep"></span><button id="lclose">Close</button></div>
      <div class="lbody"><canvas id="lcv"></canvas><div class="lside"><div class="lview"><small>What Opus is looking at</small><img id="lviewimg" alt=""></div>
      <div id="lclasses"></div></div></div><div class="lstrip" id="lstrip"></div></div>`);
    $("lclose").onclick = stop;
    new ResizeObserver(() => draw(shown)).observe($("lcv").parentElement);
  }
  function stop() { clearTimeout(timer); job = null; $("livep")?.remove(); versions = []; shown = -1; pic = null; }

  async function start() {
    const p = typeof current !== "undefined" && current ? current.path : null;
    if (!p) return;
    stop(); panel();
    $("lstage").textContent = "Starting the reading…";
    const r = await fetch("/bob/read?path=" + encodeURIComponent(p)).then((x) => x.json()).catch((e) => ({ error: e.message }));
    if (r.error) { $("lstage").textContent = "Could not start: " + r.error; return; }
    job = r.id; lastPartial = -1; lastView = -1;
    poll();
  }
  window.bobLiveReading = start;
  $("vlive").onclick = start;

  async function poll() {
    if (!job) return;
    try {
      st = await fetch("/api/v2/status?id=" + encodeURIComponent(job)).then((x) => x.json());
    } catch (e) { timer = setTimeout(poll, 2500); return; }
    const reading = st.reading, stage = st.stage;
    $("lstage").textContent = stage === "error" ? "Failed: " + (st.detail || "") : stage !== "done" ? (st.label || stage) + "…"
      : reading === "running" ? `Opus is reading the drawing · ${Math.round(st.reading_elapsed || 0)} s` : reading === "done" ? `Opus reading complete · ${Math.round(st.reading_seconds || 0)} s` :
      reading === "failed" ? "Opus reading did not finish: showing our models' reading" : "Ready";
    $("lstep").textContent = st.reading_step ? "· " + st.reading_step : "";
    if (stage === "done" && !pic) {
      pic = new Image();
      pic.onload = () => draw(shown);
      pic.src = "/api/v2/picture?id=" + encodeURIComponent(job);
      picScale = st.display_scale || 1;
    }
    if (st.view_n && st.view_n !== lastView) {
      lastView = st.view_n;
      $("lviewimg").src = `/api/v2/view?id=${encodeURIComponent(job)}&n=${st.view_n}`;
      $("lviewimg").parentElement.classList.add("on");
    }
    const key = stage !== "done" ? -1 : reading === "done" ? 1e9 : (st.partial_n || 0);
    if (stage === "done" && key !== lastPartial) {
      lastPartial = key;
      const layer = await fetch("/api/v2/layer?id=" + encodeURIComponent(job)).then((x) => x.json()).catch(() => null);
      if (layer) addVersion(layer, versions.length === 0 ? "Our models" : reading === "done" ? "Opus: final" : `Opus step ${st.partial_n}`);
    }
    if (stage === "error" || (stage === "done" && reading !== "running")) return;
    timer = setTimeout(poll, 1500);
  }

  function addVersion(layer, label) {
    const counts = layer.filter((c) => !c.hidden).map((c) => [c.name, (c.items || []).length]);
    versions.push({ layer, label, counts, at: st ? Math.round((st.reading_elapsed || st.elapsed || 0)) : 0 });
    strip();
    reveal(versions.length - 1);
  }
  function strip() {
    $("lstrip").innerHTML = versions.map((v, i) => `<button class="${i === shown ? "on" : ""}" data-v="${i}" title="${esc(v.label)}">${i === 0 ? "MODELS" : "V" + (i + 1)}</button>`).join("") +
      `<span class="muted">${versions.length} version${versions.length === 1 ? "" : "s"} · click one to see it</span>`;
    $("lstrip").querySelectorAll("button").forEach((b) => b.onclick = () => reveal(+b.dataset.v));
  }

  // ---- drawing: the page picture, every item of the reading outlined, one dash pattern per kind; pixel wipe between versions
  const DASH = [[], [6, 3], [2, 2], [10, 3, 2, 3], [1, 3], [8, 2, 2, 2, 2, 2]];
  function geom(cv) {
    const W = cv.width, H = cv.height, s = Math.min(W / pic.naturalWidth, H / pic.naturalHeight);
    return { s, ox: (W - pic.naturalWidth * s) / 2, oy: (H - pic.naturalHeight * s) / 2 };
  }
  function render(g, cv, v) {
    g.fillStyle = "#fefefe"; g.fillRect(0, 0, cv.width, cv.height);
    if (!pic || !pic.complete || !pic.naturalWidth) return;
    const { s, ox, oy } = geom(cv), k = s * picScale;             // layer px -> picture px (display_scale) -> canvas px
    g.globalAlpha = 0.45; g.drawImage(pic, ox, oy, pic.naturalWidth * s, pic.naturalHeight * s); g.globalAlpha = 1;
    if (!v) return;
    const X = (x) => ox + x * k, Y = (y) => oy + y * k;
    v.layer.filter((c) => !c.hidden).forEach((c, ci) => {
      g.setLineDash(DASH[ci % DASH.length]); g.strokeStyle = "#1e1e1e"; g.lineWidth = 1.6;
      for (const it of c.items || []) {
        const pts = it.polygon || it.polyline;
        if (pts && pts.length > 1) {
          g.beginPath(); g.moveTo(X(pts[0][0]), Y(pts[0][1])); for (const p of pts.slice(1)) g.lineTo(X(p[0]), Y(p[1])); if (it.polygon) g.closePath(); g.stroke();
        } else if (it.bbox) {
          const [a, b, c2, d] = it.bbox; g.strokeRect(X(a), Y(b), (c2 - a) * k, (d - b) * k);
        } else if (it.point) {
          g.fillStyle = "#1e1e1e"; g.fillRect(X(it.point[0]) - 2, Y(it.point[1]) - 2, 4, 4);
        }
      }
    });
    g.setLineDash([]);
  }
  function draw(i) {
    const cv = $("lcv");
    if (!cv) return;
    const r = cv.parentElement.getBoundingClientRect();
    cv.width = Math.max(200, r.width - 230); cv.height = Math.max(200, r.height);
    render(cv.getContext("2d"), cv, versions[i]);
    side(i);
  }
  function side(i) {
    const v = versions[i], prev = versions[i - 1];
    if (!v || !$("lclasses")) return;
    const before = new Map(prev ? prev.counts : []);
    $("lclasses").innerHTML = `<b>${esc(v.label)}</b><table>${v.counts.map(([n, c], ci) => {
      const d = prev ? c - (before.get(n) || 0) : 0;
      return `<tr><td><i class="dash d${ci % DASH.length}"></i>${esc(n)}</td><td>${c}</td><td class="${d > 0 ? "up" : d < 0 ? "down" : ""}">${prev && d ? (d > 0 ? "+" : "") + d : ""}</td></tr>`;
    }).join("")}</table>`;
  }
  function reveal(i) {
    const cv = $("lcv");
    if (!cv || !versions[i]) return;
    if (wiping || shown < 0 || !pic || !pic.complete) { shown = i; strip(); draw(i); return; }
    wiping = true;
    const W = cv.width, H = cv.height, g = cv.getContext("2d");
    const a = document.createElement("canvas"); a.width = W; a.height = H; render(a.getContext("2d"), a, versions[shown]);
    const b = document.createElement("canvas"); b.width = W; b.height = H; render(b.getContext("2d"), b, versions[i]);
    const p = document.createElement("canvas"); p.width = p.height = 8;          // the UI's diagonal hatch
    const pg = p.getContext("2d"); pg.fillStyle = "#fefefe"; pg.fillRect(0, 0, 8, 8); pg.fillStyle = "#1e1e1e";
    for (let q = 0; q < 8; q++) { pg.fillRect(q, 7 - q, 1, 1); pg.fillRect((q + 4) % 8, 7 - q, 1, 1); }
    const pat = g.createPattern(p, "repeat"), B = 10, cols = Math.ceil(W / B), rows = Math.ceil(H / B), band = 4;
    const jag = Array.from({ length: cols }, (_, c) => (Math.sin(c * 1.3) + Math.sin(c * 0.41) + 2) * 1.4 + (c % 2));
    const t0 = performance.now(), T = 900;
    shown = i; strip(); side(i);
    const step = (now) => {
      const f = Math.min(1, (now - t0) / T) * (rows + band + 8);
      g.drawImage(a, 0, 0);
      for (let c = 0; c < cols; c++) {
        const r = Math.floor(f - jag[c]);
        if (r <= 0) continue;
        const top = Math.max(0, r - band);
        g.drawImage(b, c * B, 0, B, top * B, c * B, 0, B, top * B);          // the new version above the edge
        g.fillStyle = pat; g.fillRect(c * B, top * B, B, (r - top) * B);    // the falling hatch band
      }
      if (now - t0 < T) requestAnimationFrame(step);
      else { g.drawImage(b, 0, 0); wiping = false; }
    };
    requestAnimationFrame(step);
  }

  // a file you upload yourself is read live at once
  const real = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const res = await real(input, init);
    try {
      const url = typeof input === "string" ? input : input.url;
      if (url.startsWith("/api/upload")) res.clone().json().then((d) => { if (d && d.path) setTimeout(start, 1500); }).catch(() => {});
    } catch (e) { /* never break the page */ }
    return res;
  };
})();
