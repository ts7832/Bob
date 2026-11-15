// Bob's opening screen (waving, a speech bubble typed out), the pixel flood transition to the projects screen, and the
// little "Bob is working" badge shown while anything is being read, answered or corrected. Sprites: ui/bob/mascot/.
(() => {
  const $ = (id) => document.getElementById(id);
  const M = "/bob/mascot/";
  // the working badge: a 32 x 32 Bob (icons32/), chosen in the gallery (/bob/mascot/choose.html); default: with a clipboard
  const ICONS = { work_e: 8, work_k1: 9, work_k2: 9, work_k3: 9, work_k4: 9, work_k5: 4, b1_bust: 1, b2_wink: 4, b3_wave: 4, b4_clipboard: 9, b5_head: 1, b6_head_clipboard: 9 };
  let ICON = "work_e";                     // E (peeking over the clipboard, writing): the user's pick for working / loading
  try { const k = localStorage.getItem("bobWorkIcon32"); if (k === "work_e" && ICONS[k]) ICON = k; } catch (e) { /* */ }

  // ------------------------------------------------------------------ the pixel flood: diagonal hatch, pixel blocks, top to bottom
  function flood(onCovered, onDone) {
    const cv = document.createElement("canvas");
    cv.id = "bobflood";
    document.body.appendChild(cv);
    const dpr = 1, W = cv.width = innerWidth * dpr, H = cv.height = innerHeight * dpr, g = cv.getContext("2d");
    const B = 12, cols = Math.ceil(W / B), rows = Math.ceil(H / B);
    // the hatch: 1-bit diagonal lines, crisp pixels
    const p = document.createElement("canvas"); p.width = p.height = 8;
    const pg = p.getContext("2d"); pg.fillStyle = "#fefefe"; pg.fillRect(0, 0, 8, 8); pg.fillStyle = "#1e1e1e";
    for (let i = 0; i < 8; i++) { pg.fillRect(i, 7 - i, 1, 1); pg.fillRect((i + 4) % 8, 7 - i, 1, 1); }
    const pat = g.createPattern(p, "repeat");
    const jag = Array.from({ length: cols }, (_, c) => (Math.sin(c * 1.7) + Math.sin(c * 0.53) + 2) * 1.6 + (c % 3));   // ragged pixel front
    const t0 = performance.now(), T1 = 650, T2 = 650;
    let covered = false;
    const step = (now) => {
      const t = now - t0;
      g.clearRect(0, 0, W, H);
      g.fillStyle = pat;
      if (t < T1) {                                          // phase 1: lines flood down over the screen
        const f = t / T1 * (rows + 8);
        for (let c = 0; c < cols; c++) { const r = Math.max(0, Math.floor(f - jag[c])); g.fillRect(c * B, 0, B, r * B); }
      } else {
        if (!covered) { covered = true; onCovered && onCovered(); }
        const f = (t - T1) / T2 * (rows + 8);               // phase 2: the lines keep falling, the next page shows from the top
        for (let c = 0; c < cols; c++) { const r = Math.max(0, Math.floor(f - jag[c])); g.fillRect(c * B, r * B, B, H - r * B); }
        if (t > T1 + T2) { cv.remove(); onDone && onDone(); return; }
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  // (the transitions live in flood.js: window.bobFlood(variant, onCovered, onDone))

  // ------------------------------------------------------------------ opening screen
  const HELLOS = { hello_1: 8, hello_2: 6, hello_3: 4, hello_4: 4, hello_5: 4 };
  let HELLO = "hello_1", FLOOD = "rain";
  try { const h = localStorage.getItem("bobHello"); if (HELLOS[h]) HELLO = h; FLOOD = localStorage.getItem("bobFlood") || FLOOD; } catch (e) { /* */ }
  const LINES = ["Hi, I'm Bob.", "I read your blueprints and answer questions about them: doors, rooms, areas, floors.",
                 "Every number is marked on the drawing, so you can check it."];
  function splash() {
    if (/open=/.test(location.hash)) return;                 // demo deep links go straight to the file
    try { if (sessionStorage.getItem("bobSeen")) return; } catch (e) { /* */ }
    document.body.insertAdjacentHTML("beforeend", `<div id="bobsplash"><div class="spl">
      <div class="bobsprite hello" role="img" aria-label="Bob, a pixel builder, saying hello" style="background-image:url(${M}${HELLO}.png);--n:${HELLOS[HELLO]}"></div>
      <div class="bubble"><p id="bubtext"></p><span class="caret"></span></div></div>
      <div class="spl-hint" id="splhint">click anywhere to start ▸</div></div>`);
    const el = $("bobsplash"), out = $("bubtext");
    let li = 0, ci = 0, typing = true;
    const type = () => {
      if (!document.body.contains(out)) return;
      if (li >= LINES.length) { typing = false; $("splhint").classList.add("on"); return; }
      const line = LINES[li];
      if (ci === 0 && li > 0) out.insertAdjacentHTML("beforeend", "<br>");
      out.insertAdjacentText("beforeend", line[ci++]);
      if (ci >= line.length) { li++; ci = 0; setTimeout(type, 420); }
      else setTimeout(type, /[,.:]/.test(line[ci - 1]) ? 160 : 34);
    };
    setTimeout(type, 500);
    const go = () => {
      el.onclick = null; document.removeEventListener("keydown", key);
      try { sessionStorage.setItem("bobSeen", "1"); } catch (e) { /* */ }
      (window.bobFlood ? (cb) => window.bobFlood(FLOOD, cb) : flood)(() => el.remove());
    };
    const key = (e) => { if (e.key === "Enter" || e.key === " " || e.key === "Escape") go(); };
    el.onclick = go; document.addEventListener("keydown", key);
  }
  splash();

  // ------------------------------------------------------------------ Bob is working (anything being read, answered, corrected)
  document.body.insertAdjacentHTML("beforeend", `<div id="bobwork" hidden><div class="bobsprite work" style="background-image:url(${M}${ICON.startsWith("work_") ? "" : "icons32/"}${ICON}.png);--n:${ICONS[ICON]}"></div><span id="bobworkt">Bob is working…</span></div>`);
  let busy = 0;
  const show = () => { $("bobwork").hidden = busy <= 0; };
  window.bobWorking = (on, label) => { busy += on ? 1 : -1; if (label && on) $("bobworkt").textContent = label; show(); };
  const WHAT = [[/\/notes$/, "Bob is looking at the circled part…"], [/\/survey$/, "Bob is reading the whole project…"],
                [/\/api\/solve|\/api\/ask/, "Bob is working out the answer…"], [/\/corrections|\/revert_to/, "Bob is updating the drawing…"],
                [/\/bob\/upload/, "Bob is filing the blueprint…"], [/\/api\/open|\/api\/upload/, "Bob is reading the blueprint…"]];
  const real = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    const hit = WHAT.find(([re]) => re.test(url.split("?")[0]));
    if (!hit) return real(input, init);
    window.bobWorking(true, hit[1]);
    try { return await real(input, init); } finally { window.bobWorking(false); }
  };
})();
