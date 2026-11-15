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

  let job = null, timer = 0, versions = [], shown = -1, pic = null, picScale = 1, wiping = false, lastPartial = -1, lastView = -1, st = null, misses = 0;

  function panel() {
    if ($("livep")) return;
    vwin.insertAdjacentHTML("beforeend", `<div id="livep"><div class="lhead"><b id="lstage">Starting…</b><span id="lstep"></span><button id="lclose">Close</button></div>
      <div class="lbody"><canvas id="lcv"></canvas><div class="lside"><div class="lview"><small>What Opus is looking at</small><img id="lviewimg" alt=""></div>
      <div id="lclasses"></div></div></div><div class="lstrip" id="lstrip"></div></div>`);
    $("lclose").onclick = stop;
    new ResizeObserver(() => draw(shown)).observe($("lcv").parentElement);
  }
  function stop() { clearTimeout(timer); job = null; $("livep")?.remove(); versions = []; shown = -1; pic = null; }
  // until our models have read it: the blueprint itself, faded, with Bob working on top (never a white panel)
  async function placeholder() {
    try {
      const rd = current && current.renderP ? await current.renderP : null;
      if (rd && rd.url && !pic) {
        pic = new Image();
        pic.onload = () => draw(shown);
        pic.src = rd.url;
        picScale = (pic.naturalWidth && rd.width) ? 1 : 1;
      }
    } catch (e) { /* the picture comes with the models' reading */ }
    const body = document.querySelector("#livep .lbody");
    if (body && !$("lwait")) body.insertAdjacentHTML("beforeend", '<div id="lwait"><div class="bobsprite work" style="background-image:url(/bob/mascot/work_e.png);--n:8"></div><b id="lwaitt">Our models are reading the drawing…</b></div>');
  }

  async function start(retry) {
    const p = typeof current !== "undefined" && current ? current.path : null;
    if (!p) return;
    stop(); panel();
    $("lstage").textContent = "Starting the reading…";
    placeholder();
    const r = await fetch("/bob/read?path=" + encodeURIComponent(p) + (retry === true ? "&retry=1" : "")).then((x) => x.json()).catch((e) => ({ error: e.message }));
    if (r.error) { $("lstage").textContent = "Could not start: " + r.error; return; }
    job = r.id; lastPartial = -1; lastView = -1; misses = 0;
    if (r.limit) window.bobOpusLimit = r.limit;
    poll();
  }
  window.bobLiveReading = start;
  $("vlive").onclick = start;

  // ---- Opus status of the open blueprint: not run / reading (seconds, step) / done; click to watch
  tools.insertAdjacentHTML("afterbegin", '<span id="opuschip" title="Opus reading of this blueprint (click to watch)">OPUS · …</span>');
  let chipT = 0;
  async function chip() {
    clearTimeout(chipT);
    const p = typeof current !== "undefined" && current ? current.path : null;
    if (!p) { $("opuschip").textContent = "OPUS · –"; return; }
    let r = {};
    try { r = await fetch("/bob/readstatus?path=" + encodeURIComponent(p)).then((x) => x.json()); } catch (e) { /* */ }
    if (r.limit) window.bobOpusLimit = r.limit;
    const c = $("opuschip");
    c.className = "";
    if (!r.started) { c.textContent = "OPUS · NOT RUN ▸"; c.classList.add("off"); }
    else if (r.stage !== "done") { c.textContent = "OPUS · MODELS READING…"; c.classList.add("busy"); }
    else if (r.reading === "running") { c.textContent = `OPUS · READING ${Math.round(r.reading_elapsed || 0)} / ${r.limit || 240} s · ${r.partial_n || 0} STEP${r.partial_n === 1 ? "" : "S"}`; c.classList.add("busy"); }
    else if (r.reading === "done") { c.textContent = "OPUS · DONE ✓"; c.classList.add("ok"); }
    else if (r.reading === "failed") { c.textContent = "OPUS · DID NOT FINISH"; c.classList.add("off"); }
    else { c.textContent = "OPUS · MODELS ONLY"; c.classList.add("off"); }
    if (r.started && (r.stage !== "done" || r.reading === "running")) chipT = setTimeout(chip, 3000);
  }
  window.bobOpusChip = chip;
  $("opuschip").onclick = () => start();
  const t = $("title");
  if (t) new MutationObserver(() => setTimeout(chip, 800)).observe(t, { childList: true, characterData: true, subtree: true });

  async function poll() {
    if (!job) return;
    let got = null;
    try { got = await fetch("/api/v2/status?id=" + encodeURIComponent(job)).then((x) => x.json()); } catch (e) { /* */ }
    if (!got || !got.stage) {                                  // the reading is gone (the server restarted): stop waiting
      if (++misses < 5) { timer = setTimeout(poll, 2500); return; }
      $("lstage").textContent = "The reading was lost (the server restarted): try again";
      if (!$("lretry")) { $("lclose").insertAdjacentHTML("beforebegin", '<button id="lretry">Try again</button>'); $("lretry").onclick = () => start(true); }
      if ($("lwait")) $("lwait").remove();
      return;
    }
    misses = 0; st = got;
    const reading = st.reading, stage = st.stage, lim = window.bobOpusLimit || 240;
    if (reading === "running" && (st.reading_elapsed || 0) > lim + 90) {    // the server should have stopped it by now
      $("lstage").textContent = `Opus is past its ${lim} s limit: showing the best reading so far`;
      if (!$("lretry")) { $("lclose").insertAdjacentHTML("beforebegin", '<button id="lretry">Try again</button>'); $("lretry").onclick = () => start(true); }
      return;
    }
    const capped = st.discovery && st.discovery.capped;
    $("lstage").textContent = stage === "error" ? "Failed: " + (st.detail || "") : stage !== "done" ? (st.label || stage) + "…"
      : reading === "running" ? `Opus is reading the drawing · ${Math.round(st.reading_elapsed || 0)} / ${lim} s` : reading === "done" ? (capped ? `Opus reading: best result at the ${lim} s limit` : `Opus reading complete · ${Math.round(st.reading_seconds || 0)} s`) :
      reading === "failed" ? "Opus reading did not finish: showing our models' reading" : "Ready";
    if (reading === "failed" && !$("lretry")) { $("lclose").insertAdjacentHTML("beforebegin", '<button id="lretry">Try again</button>'); $("lretry").onclick = () => start(true); }
    $("lstep").textContent = st.reading_step ? "· " + st.reading_step : "";
    if ($("lwait")) {
      if (stage === "done" && versions.length) $("lwait").remove();
      else $("lwaitt").textContent = stage === "done" ? "Drawing what our models found…" : `Our models are reading the drawing… ${Math.round(st.elapsed || 0)} s`;
    }
    if (stage === "done" && pic && !String(pic.src).includes("/api/v2/picture")) pic = null;    // swap the placeholder for the reading's own picture
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
    if (window.bobOpusChip) window.bobOpusChip();
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
      return `<tr><td title="${esc(n)}"><i class="dash d${ci % DASH.length}"></i>${esc(n.replace(/_/g, " "))}</td><td>${c}</td><td class="${d > 0 ? "up" : d < 0 ? "down" : ""}">${prev && d ? (d > 0 ? "+" : "") + d : ""}</td></tr>`;
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
