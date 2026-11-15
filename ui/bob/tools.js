// Bob tools on kokoro's page: money saved, history of every question and photo, corrections that are kept, circle a
// problem (Opus proposes, a person accepts), and revisions (newer version found on upload, what changed).
// Everything is stored in the library on Modal (MariaDB), reached through library/team_server.py at /bob/lib/.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const lib = async (path, opt = {}) => {
    const r = await fetch("/bob/lib" + path, opt);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || j.error || "HTTP " + r.status);
    return j;
  };
  const post = (path, body) => lib(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
  const who = () => localStorage.getItem("bobName") || "";
  const money = (v) => (v >= 100 ? Math.round(v).toLocaleString("en") : v.toFixed(2));
  const fmtVal = (a, u) => a == null ? "cannot determine" : `${typeof a === "number" ? +a.toFixed(2) : a}${u && u !== "count" ? " " + ({ m2: "m²", sqft: "ft²", ft2: "ft²" }[u] || u) : ""}`;
  try { if (typeof TIER === "object") TIER.corrected = ["Corrected by a person", "t-corrected"]; } catch (e) { /* page without TIER */ }

  let sid = null, sidPath = null, lastRes = null, lastQ = null, mine = [];

  // ------------------------------------------------------------------ header: money saved + history
  const header = document.querySelector("header");
  if (header && !$("savings")) {
    header.insertAdjacentHTML("beforeend", `<button id="savings" title="What this system saved so far (click for how it is counted)">
        <b id="sv-eur">€–</b><small>saved</small><i></i><b id="sv-ai">$–</b><small>AI cost saved</small></button>
      <button id="histbtn" title="Every question asked and every photo taken">History</button>`);
    document.body.insertAdjacentHTML("beforeend", `<div id="svpop" class="bobwin" hidden><div class="tb"><span>Money saved</span><button class="x" data-close="svpop">×</button></div><div id="svbody"></div></div>
      <div id="histpane" class="bobwin" hidden><div class="tb"><span>History</span><button class="x" data-close="histpane">×</button></div>
        <div class="htabs"><button data-h="q" class="on">Questions</button><button data-h="p">Phone photos</button><button data-h="n">Notes to Opus</button></div><div id="histbody"></div></div>`);
    $("savings").onclick = () => { $("svpop").hidden = !$("svpop").hidden; savings(); };
    $("histbtn").onclick = () => { $("histpane").hidden = !$("histpane").hidden; if (!$("histpane").hidden) history_(); };
    document.addEventListener("click", (e) => { const c = e.target.closest("[data-close]"); if (c) $(c.dataset.close).hidden = true; });
    document.querySelectorAll("#histpane .htabs button").forEach((b) => b.onclick = () => {
      document.querySelectorAll("#histpane .htabs button").forEach((x) => x.classList.toggle("on", x === b)); histTab = b.dataset.h; drawHist();
    });
  }
  let sv = null;
  async function savings() {
    try { sv = await lib("/api/savings"); } catch (e) { return; }
    $("sv-eur").textContent = "€" + money(sv.person.eur_saved);
    $("sv-ai").textContent = "$" + money(sv.ai.saved_usd);
    $("svbody").innerHTML = `<div class="big">€${money(sv.person.eur_saved)}</div><p>${sv.person.hours_saved.toLocaleString("en")} hours of a person's take-off work:
      ${sv.answers.toLocaleString("en")} answers (${sv.computed} computed, ${sv.reused} reused from the library).</p>
      <p class="muted">Assumption: ${esc(sv.person.assumption)}.</p><hr>
      <div class="big">$${money(sv.ai.saved_usd)}</div><p>AI cost avoided: all of it through Opus would cost $${money(sv.ai.opus_for_everything_usd)}; we spent
      $${sv.ai.spent_usd.toFixed(2)} (most answers come from code, at $0, and repeats from the library).</p>
      <p class="muted">Assumption: ${esc(sv.ai.assumption)}.</p><p class="muted">${sv.scans} phone scans.</p>`;
  }
  savings();
  setInterval(savings, 30000);

  let hist = null, histTab = "q";
  async function history_() {
    $("histbody").innerHTML = '<p class="muted">Loading…</p>';
    try { hist = await lib("/api/history?limit=150"); } catch (e) { $("histbody").textContent = e.message; return; }
    drawHist();
  }
  function drawHist() {
    if (!hist) return;
    const t = (s) => esc(String(s).slice(5, 16).replace("T", " "));
    if (histTab === "q") $("histbody").innerHTML = hist.questions.map((x) => `<div class="hrow">
        <div class="hmeta">${t(x.created_at)} <span class="flag ${x.asked_from === "phone" ? "inv" : ""}">${esc(x.asked_from.toUpperCase())}</span>
          ${x.corrected ? '<span class="flag corr">CORRECTED</span>' : ""}${x.language_model ? '<span class="flag">LANGUAGE MODEL</span>' : ""}
          ${x.times_reused ? `<span class="flag">REUSED ×${x.times_reused}</span>` : ""}</div>
        <div class="hq">${esc(x.question)}</div>
        <div class="ha"><b>${esc(x.status === "pending" ? "…" : fmtVal(x.answer, x.unit))}</b>${x.corrected ? ` <s>${esc(fmtVal(x.engine_answer, x.unit))}</s>` : ""}
          <span class="muted"> · ${esc(x.tier || "")} · ${esc(x.sheet)}${x.revision ? " rev " + esc(x.revision) : ""} · ${x.cost_usd ? "$" + x.cost_usd.toFixed(4) : "$0"}</span></div></div>`).join("") || '<p class="muted">No questions yet.</p>';
    if (histTab === "p") $("histbody").innerHTML = `<div class="photos">${hist.scans.map((c) => `<figure>${c.photo ? `<img loading="lazy" src="/bob/lib${c.photo}">` : '<div class="nophoto">no photo</div>'}
        <figcaption>${t(c.created_at)} · ${c.sheet ? "→ " + esc(c.sheet) : "<b>no match</b>"} · ${c.details} details · ${(+c.seconds).toFixed(1)} s</figcaption></figure>`).join("")}</div>` || "";
    if (histTab === "n") $("histbody").innerHTML = hist.notes.map((n) => `<div class="hrow"><div class="hmeta">${t(n.created_at)} · ${esc(n.author || "someone")} · ${esc(n.sheet)}</div>
        <div class="hq">${esc(n.note || "(no comment)")}</div><div class="muted">${n.proposals} proposal(s) from Opus, ${n.accepted} accepted</div></div>`).join("") || '<p class="muted">No notes yet.</p>';
  }

  // ------------------------------------------------------------------ which blueprint is open (library id), revisions
  async function sheetIdFor(path) {
    if (sidPath === path && sid) return sid;
    const r = await fetch("/bob/sid?path=" + encodeURIComponent(path)).then((x) => x.json());
    if (r.error) throw new Error(r.error);
    sid = r.id; sidPath = path;
    if (r.revision_suggestion) suggest(r.revision_suggestion);
    return sid;
  }
  const vwin = document.querySelector(".vwin");
  const bar = (html) => {
    let b = $("revbar");
    if (!b && vwin) { vwin.insertAdjacentHTML("afterbegin", '<div id="revbar"></div>'); b = $("revbar"); vwin.insertBefore(b, vwin.children[1]); }
    if (b) { b.innerHTML = html; b.hidden = !html; }
    return b;
  };
  let seenPath = null;
  async function onOpen() {
    const p = (typeof current !== "undefined" && current) ? current.path : null;
    if (!p || p === seenPath) return;
    seenPath = p; bar("");
    try {
      const id = await sheetIdFor(p);
      const rv = await lib(`/api/sheets/${id}/revisions`);
      if (!rv.this_is_current) {
        const newest = rv.chain[rv.chain.length - 1];
        bar(`<b>OUT OF DATE.</b> A newer version of this blueprint is in the library: ${esc(newest.name)}${newest.revision ? " (rev " + esc(newest.revision) + ")" : ""}.
             <button data-diff="${newest.id}:${id}">Show what changed</button>`);
      } else if (rv.chain.length > 1 && !$("revbar")?.textContent) {
        const prev = rv.chain[rv.chain.length - 2];
        bar(`Version ${rv.chain.length} of ${rv.chain.length}: replaces ${esc(prev.name)}. <button data-diff="${id}:${prev.id}">Show what changed</button>`);
      }
    } catch (e) { console.warn("bob: library id", e); }
  }
  function suggest(s) {
    bar(`<b>NEWER REVISION?</b> This looks like a new version of <b>${esc(s.name)}</b> (${esc(s.reason)}). It will replace it; anyone scanning the old paper is told.
         <button id="revyes">Yes, it replaces it</button><button id="revno">No</button>`);
    $("revyes").onclick = async () => {
      await post(`/api/sheets/${sid}/revision_of/${s.id}`);
      bar(`Saved: this blueprint replaces ${esc(s.name)}. <button data-diff="${sid}:${s.id}">Show what changed</button>`);
    };
    $("revno").onclick = () => bar("");
  }
  document.addEventListener("click", (e) => {
    const d = e.target.closest("[data-diff]");
    if (d) { const [a, b] = d.dataset.diff.split(":"); showDiff(+a, +b); }
  });
  async function showDiff(newId, oldId) {
    modal("What changed", `<div class="diffwrap"><div class="diffimg"><img id="diffimg" src="/bob/lib/api/sheets/${newId}/diff/${oldId}"></div>
      <div class="diffside"><p><span class="sw red"></span> only on the old version (removed) <br><span class="sw blue"></span> only on the new version (added)<br>
      <span class="sw grey"></span> unchanged. Boxes: changed areas.</p><div id="diffinfo" class="muted"></div><div id="cmp"></div></div></div>`);
    lib(`/api/sheets/${newId}/diff/${oldId}?info=1`).then((m) => { $("diffinfo").textContent = `${m.regions} changed area(s) found by lining the two versions up.`; }).catch(() => {});
    const cmp = async () => {
      const c = await lib(`/api/sheets/${newId}/compare/${oldId}`);
      $("cmp").innerHTML = (c.rows.length ? `<table><tr><th>Question</th><th>old</th><th>new</th></tr>${c.rows.map((r) =>
          `<tr class="${r.changed ? "chg" : ""}"><td>${esc(r.question)}</td><td>${esc(fmtVal(r.old))}</td><td><b>${esc(fmtVal(r.new))}</b></td></tr>`).join("")}</table>` : "")
        + (c.only_old.length ? `<p class="muted">${c.only_old.length} question(s) were asked on the old version only.</p><button id="reask">Ask them on the new version</button>` : "");
      if ($("reask")) $("reask").onclick = async () => {
        $("reask").disabled = true; $("reask").textContent = "Asking…";
        for (const question of c.only_old) { try { await post("/api/solve", { sheet_id: newId, question }); } catch (e) { /* keep going */ } }
        cmp();
      };
    };
    cmp().catch((e) => { $("cmp").textContent = e.message; });
  }
  const title = $("title");
  if (title) new MutationObserver(() => setTimeout(onOpen, 300)).observe(title, { childList: true, characterData: true, subtree: true });

  // ------------------------------------------------------------------ generic modal window
  function modal(name, html) {
    let v = $("bobveil");
    if (!v) {
      document.body.insertAdjacentHTML("beforeend", '<div id="bobveil"><div class="bobwin" id="bobmodal"><div class="tb"><span id="bobmtitle"></span><button class="x" id="bobmx">×</button></div><div id="bobmbody"></div></div></div>');
      v = $("bobveil");
      $("bobmx").onclick = () => closeModal();
      v.onclick = (e) => { if (e.target === v) closeModal(); };
    }
    $("bobmtitle").textContent = name; $("bobmbody").innerHTML = html; v.classList.add("on");
  }
  let onClose = null;
  function closeModal() { $("bobveil").classList.remove("on"); $("bobmbody").innerHTML = ""; if (onClose) { const f = onClose; onClose = null; f(); } }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("bobveil")?.classList.contains("on")) closeModal(); });

  // ------------------------------------------------------------------ answers: keep the result, add the correction tools
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const res = await realFetch(input, init);
    try {
      const url = typeof input === "string" ? input : input.url;
      if (url.includes("/api/ask") && !url.includes("/bob/")) {
        try { lastQ = JSON.parse(init.body).question; } catch (e) { /* */ }
        res.clone().json().then((r) => { lastRes = r; setTimeout(tools, 60); setTimeout(savings, 800); }).catch(() => {});
      }
    } catch (e) { /* never break the page */ }
    return res;
  };
  // kokoro's page redraws the answer box when a result arrives: put the tools back after every redraw
  const ansBox = $("answer");
  if (ansBox) new MutationObserver(() => {
    if (lastRes && !$("bobtools") && !/Working on it/.test(ansBox.textContent) && ansBox.textContent.trim()) tools();
  }).observe(ansBox, { childList: true });
  const reask = () => { if (lastQ && $("q") && $("askbtn")) { $("q").value = lastQ; $("askbtn").click(); } };

  function tools() {
    const box = $("answer");
    if (!box || !lastRes) return;
    $("bobtools")?.remove();
    const r = lastRes, c = r.corrected;
    const corrected = c ? `<div class="corrnote"><b>CORRECTED</b> by ${esc(c.by || "a person")}: the system said <s>${esc(fmtVal(r.engine_answer, r.unit))}</s>, now
        <b>${esc(fmtVal(r.answer, r.unit))}</b>.${mine.length ? ' <button class="link" id="undolast">undo my last correction</button>' : ""}</div>` : "";
    box.insertAdjacentHTML("beforeend", `<div id="bobtools">${corrected}
      <div class="row"><button id="fixbtn">✎ Correct this answer</button><button id="circlebtn">◯ Circle a problem</button>
      <button id="savecheck" hidden>Keep my check</button></div><div id="fixform"></div></div>`);
    $("fixbtn").onclick = fixForm;
    $("circlebtn").onclick = openMarkup;
    $("savecheck").onclick = saveCheck;
    if ($("undolast")) $("undolast").onclick = async () => { const id = mine.pop(); await post(`/api/corrections/${id}/undo`); reask(); };
    watchCheck();
  }
  // kokoro's page lets a person leave out marks and add missed ones on screen; this keeps that check in the library
  function watchCheck() {
    const show = () => {
      const w = typeof work !== "undefined" ? work : null;
      if ($("savecheck")) $("savecheck").hidden = !(w && (w.excluded.size || w.added));
    };
    show();
    const box = $("answer");
    if (box && !box._bobObs) { box._bobObs = new MutationObserver(() => setTimeout(show, 30)); box._bobObs.observe(box, { childList: true, subtree: true }); }
  }
  async function saveCheck() {
    const w = work, ev = lastRes?.evidence || [];
    const clsOf = (id) => (ev.find((e) => e.id === id) || {}).cls || lastRes?.intent?.cls || (w.items.find((i) => i.id === id) || {}).kind || "item";
    const main = lastRes?.intent?.cls || (ev[0] || {}).cls || (w.items[0] || {}).kind || "item";
    $("savecheck").disabled = true; $("savecheck").textContent = "Saving…";
    for (const id of w.excluded) mine.push((await post(`/api/sheets/${sid}/corrections`, { kind: "remove", item_id: id, cls: clsOf(id), author: who(), note: "left out on screen" })).id);
    for (let i = 0; i < w.added; i++) mine.push((await post(`/api/sheets/${sid}/corrections`, { kind: "add", cls: main, author: who(), note: "added on screen" })).id);
    reask();
  }
  function fixForm() {
    $("fixform").innerHTML = `<div class="fix"><input id="fixval" placeholder="the right answer (number)"><input id="fixnote" placeholder="why / where (optional)">
      <input id="fixwho" placeholder="your name" value="${esc(who())}"><button id="fixsave">Save correction</button></div>`;
    $("fixval").focus();
    $("fixsave").onclick = async () => {
      const v = $("fixval").value.trim();
      if (!v) return;
      localStorage.setItem("bobName", $("fixwho").value.trim());
      mine.push((await post(`/api/sheets/${sid}/corrections`, { kind: "answer", question: lastQ, value: v, note: $("fixnote").value.trim(), author: who() })).id);
      reask();
    };
  }

  // ------------------------------------------------------------------ circle a problem: Opus proposes, a person accepts
  async function openMarkup() {
    const path = current && current.path;
    if (!path) return;
    await sheetIdFor(path);
    modal("Circle a problem", `<div class="mk"><div id="mkstage"><canvas id="mkc"></canvas><div id="mkhint">Draw a circle round what is missing or wrong · wheel: zoom · shift + drag: move</div></div>
      <div class="mkside"><p>Circle the part of the blueprint, say what is wrong, and Opus looks at that part and proposes fixes. Nothing changes until you accept.</p>
      <textarea id="mkcomment" placeholder="e.g. there is a door here that was not counted"></textarea>
      <input id="mkwho" placeholder="your name" value="${esc(who())}"><button id="mksend" disabled>Send to Opus</button><div id="mkout"></div></div></div>`);
    const cv = $("mkc"), g = cv.getContext("2d"), img = new Image();
    let s = 1, ox = 0, oy = 0, pts = [], drawing = false, panning = null, props = [], accepted = 0;
    const stage = $("mkstage");
    const fit = () => {
      cv.width = stage.clientWidth; cv.height = stage.clientHeight;
      s = Math.min(cv.width / img.width, cv.height / img.height); ox = (cv.width - img.width * s) / 2; oy = (cv.height - img.height * s) / 2; draw();
    };
    const toImg = (e) => { const b = cv.getBoundingClientRect(); return [(e.clientX - b.left - ox) / s, (e.clientY - b.top - oy) / s]; };
    const box = () => {
      if (pts.length < 3) return null;
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys), px = (x1 - x0) * 0.08 + 8, py = (y1 - y0) * 0.08 + 8;
      return [Math.max(0, x0 - px), Math.max(0, y0 - py), Math.min(img.width, x1 + px), Math.min(img.height, y1 + py)];
    };
    function draw() {
      g.setTransform(1, 0, 0, 1, 0, 0); g.fillStyle = "#fefefe"; g.fillRect(0, 0, cv.width, cv.height);
      g.setTransform(s, 0, 0, s, ox, oy); g.drawImage(img, 0, 0);
      g.lineWidth = 3 / s; g.strokeStyle = "#1e1e1e"; g.setLineDash([]);
      if (pts.length > 1) { g.beginPath(); g.moveTo(...pts[0]); pts.forEach((p) => g.lineTo(...p)); if (!drawing) g.closePath(); g.stroke(); }
      for (const p of props) {
        if (!p.bbox) continue;
        const [a, b, c2, d] = p.bbox;
        g.setLineDash([8 / s, 5 / s]); g.lineWidth = 3 / s;
        g.strokeStyle = p.status === "applied" ? "#03804a" : p.status === "rejected" ? "#999" : p.kind === "remove" ? "#c0261c" : "#1e5fbf";
        g.strokeRect(a, b, c2 - a, d - b);
        g.setLineDash([]); g.fillStyle = g.strokeStyle; g.font = `${14 / s}px monospace`; g.fillText(`${p.n}. ${p.kind} ${p.cls || ""}`, a, b - 4 / s);
      }
    }
    img.onload = fit;
    img.src = `/bob/lib/api/sheets/${sid}/view`;
    cv.onwheel = (e) => {
      e.preventDefault();
      const b = cv.getBoundingClientRect(), mx = e.clientX - b.left, my = e.clientY - b.top, k = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      ox = mx - (mx - ox) * k; oy = my - (my - oy) * k; s *= k; draw();
    };
    cv.onpointerdown = (e) => {
      cv.setPointerCapture(e.pointerId);
      if (e.shiftKey || e.button !== 0) { panning = [e.clientX - ox, e.clientY - oy]; return; }
      drawing = true; pts = [toImg(e)]; props = []; draw();
    };
    cv.onpointermove = (e) => {
      if (panning) { ox = e.clientX - panning[0]; oy = e.clientY - panning[1]; draw(); return; }
      if (drawing) { pts.push(toImg(e)); draw(); }
    };
    cv.onpointerup = () => { panning = null; if (drawing) { drawing = false; draw(); $("mksend").disabled = !box(); } };
    cv.oncontextmenu = (e) => e.preventDefault();
    $("mksend").onclick = async () => {
      const bb = box();
      if (!bb) return;
      localStorage.setItem("bobName", $("mkwho").value.trim());
      const [x0, y0, x1, y1] = bb, k = Math.min(1, 1400 / Math.max(x1 - x0, y1 - y0));
      const crop = document.createElement("canvas");
      crop.width = Math.round((x1 - x0) * k); crop.height = Math.round((y1 - y0) * k);
      crop.getContext("2d").drawImage(img, x0, y0, x1 - x0, y1 - y0, 0, 0, crop.width, crop.height);
      $("mksend").disabled = true; $("mkout").innerHTML = '<p><span class="spinner"></span>Opus is looking at the circled part…</p>';
      try {
        const ev = lastRes?.evidence || [];
        const r = await post(`/api/sheets/${sid}/notes`, { image: crop.toDataURL("image/jpeg", 0.9), box: bb, comment: $("mkcomment").value.trim(), author: who(),
          question: lastQ, answer: lastRes ? fmtVal(lastRes.answer, lastRes.unit) : null, evidence_classes: [...new Set(ev.map((e) => e.cls).filter(Boolean))] });
        props = r.proposals.map((p, i) => ({ ...p, n: i + 1, status: "proposed" }));
        draw();
        const row = (p) => `<div class="prop" data-id="${p.id}"><b>${p.n}. ${esc(p.kind)}${p.cls ? " " + esc(p.cls) : ""}${p.value != null ? " = " + esc(p.value) : ""}</b>
            <span class="muted">${esc(p.reason || "")}</span><div><button data-act="accept">Accept</button><button data-act="reject">Reject</button></div></div>`;
        $("mkout").innerHTML = `<p><b>Opus:</b> ${esc(r.summary || "")}</p>${props.map(row).join("") || '<p class="muted">No fixes proposed.</p>'}
          <p class="muted small">${esc(r.model)} · $${(+r.cost_usd).toFixed(4)} · ${r.seconds} s${r.paid_by ? " · paid by " + esc(r.paid_by) : ""} · ${r.tokens.input} in / ${r.tokens.output} out tokens</p>`;
        $("mkout").querySelectorAll(".prop button").forEach((b) => b.onclick = async () => {
          const el = b.closest(".prop"), id = +el.dataset.id, act = b.dataset.act;
          await post(`/api/corrections/${id}/${act}`);
          const p = props.find((x) => x.id === id);
          p.status = act === "accept" ? "applied" : "rejected";
          if (act === "accept") { accepted++; mine.push(id); }
          el.classList.add(p.status); el.querySelector("div").innerHTML = `<b>${act === "accept" ? "ACCEPTED" : "REJECTED"}</b>`;
          draw();
        });
      } catch (e) { $("mkout").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
      $("mksend").disabled = false;
    };
    onClose = () => { if (accepted) reask(); };
    window.addEventListener("resize", fit, { once: true });
  }
})();
