// Bob projects on kokoro's page: the start screen lists projects; a project holds many blueprints (upload several at
// once), "Analyze project" has Opus survey the whole set (what each sheet is, which floor) and the engine answer the
// standard questions per floor; then switch floors and ask about one floor or the whole project.
// Also: the blueprint / side panel split can be dragged, and the microphone says what to do when the browser cannot listen.
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
  const UNIT = { m2: "m²", sqft: "ft²", ft2: "ft²" };
  const fmt = (a, u) => a == null ? "–" : `${typeof a === "number" ? +a.toFixed(2) : a}${u && u !== "count" ? " " + (UNIT[u] || u) : ""}`;
  const STD = [["Doors", "How many doors are shown on this floor plan?"], ["Windows", "How many windows are shown on this floor plan?"],
               ["Rooms", "How many rooms are there on this floor plan?"], ["Floor area", "What is the total floor area of this floor plan?"]];
  const isPlan = (s) => !s.kind || /plan/i.test(s.kind) && !/site|roof/i.test(s.kind);

  let proj = null;                                       // the open project: {id, name, sheets, survey}
  window.bobProject = () => proj;

  // ------------------------------------------------------------------ start screen
  const main = document.querySelector("main");
  document.body.insertAdjacentHTML("beforeend", '<div id="projhome" hidden></div>');
  const home = $("projhome");
  const header = document.querySelector("header");
  header.querySelector(".brand")?.insertAdjacentHTML("afterend", '<button id="projbtn" title="All projects">▤ Projects</button><button id="projchip" hidden></button>');
  $("projbtn").onclick = () => showHome();
  $("projchip").onclick = () => proj && showProject(proj.id);

  async function showHome() {
    home.hidden = false;
    home.innerHTML = `<div class="bobwin phwin"><div class="tb"><span>Projects</span><i>workspace Demo</i></div><div class="phbody"><p class="muted">Loading…</p></div></div>`;
    let ps = [];
    try { ps = await lib("/api/projects"); } catch (e) { home.querySelector(".phbody").textContent = e.message; return; }
    home.querySelector(".phbody").innerHTML = `<div class="pgrid">
      <div class="pcard new" id="newcard"><div class="plus">+</div><b>New project</b><small>name it, then add blueprints</small></div>
      ${ps.map((p) => `<div class="pcard" data-id="${p.id}">
        <div class="pthumbs">${(p.thumbs || []).map((t) => `<img loading="lazy" src="/bob/lib/api/sheets/${t}/thumb" alt="">`).join("") || '<div class="pempty">no blueprints yet</div>'}</div>
        <b>${esc(p.name)}</b><small>${p.sheets} blueprint${p.sheets === 1 ? "" : "s"} · ${p.questions} question${p.questions === 1 ? "" : "s"} · ${p.surveyed_at ? "analyzed" : "not analyzed yet"}</small></div>`).join("")}</div>
      <p class="muted small">Or close this and open a single blueprint from the panel on the right. <button class="link" id="phclose">Close</button></p>`;
    home.querySelectorAll(".pcard[data-id]").forEach((c) => c.onclick = () => showProject(+c.dataset.id));
    $("phclose").onclick = () => { home.hidden = true; };
    $("newcard").onclick = () => {
      $("newcard").innerHTML = `<b>New project</b><input id="newname" placeholder="project name, e.g. Crema Cafe"><button id="newgo">Create</button><small id="newmsg"></small>`;
      $("newcard").onclick = null;
      $("newname").focus();
      const go = async () => {
        const name = $("newname").value.trim();
        if (!name) return;
        try { const r = await lib("/api/projects?name=" + encodeURIComponent(name), { method: "POST" }); showProject(r.id); }
        catch (e) { $("newmsg").textContent = e.message; }
      };
      $("newgo").onclick = go;
      $("newname").onkeydown = (e) => { if (e.key === "Enter") go(); };
    };
  }

  // ------------------------------------------------------------------ one project
  async function showProject(pid) {
    home.hidden = false;
    home.innerHTML = `<div class="bobwin phwin"><div class="tb"><span>Project</span><i><button class="link" id="backall">← all projects</button></i></div><div class="phbody"><p class="muted">Loading…</p></div></div>`;
    $("backall").onclick = showHome;
    proj = await lib(`/api/projects/${pid}`);
    $("projchip").hidden = false; $("projchip").textContent = "▣ " + proj.name;
    draw();
    if (proj.surveyed_at) fillTable(false);
  }
  function draw() {
    const sv = proj.survey || {}, body = home.querySelector(".phbody");
    const plans = proj.sheets.filter(isPlan);
    body.innerHTML = `<div class="phead"><h1>${esc(proj.name)}</h1>
        <div class="pactions"><label class="pbtn" for="addfiles">+ Add blueprints</label><input type="file" id="addfiles" multiple hidden accept=".pdf,.png,.jpg,.jpeg,.svg,.dxf,.ifc,image/*,application/pdf">
        <button class="pbtn primary" id="analyze" ${proj.sheets.length ? "" : "disabled"}>Analyze project</button>
        <button class="pbtn" id="opusall" ${proj.sheets.length ? "" : "disabled"} title="Opus reads every floor one after the other (Claude Code on this laptop, a few minutes each)">Opus: read all floors</button></div></div>
      <div id="pdrop" class="pdrop">Drop blueprints here (several at once): PDF, PNG, JPG, SVG</div><div id="pprog" class="muted"></div>
      ${sv.summary ? `<div class="psum"><b>What this set is</b> <span class="flag">OPUS SURVEY</span><p>${esc(sv.summary)}</p>
        ${sv.building ? `<p><b>Building:</b> ${esc(sv.building)}</p>` : ""}${(sv.levels || []).length ? `<p><b>Floors (top first):</b> ${sv.levels.map(esc).join(" · ")}</p>` : ""}
        ${sv.scale_notes ? `<p><b>Scale:</b> ${esc(sv.scale_notes)}</p>` : ""}
        <p class="muted small">${esc(sv.model || "")} looked at all ${sv.sheets} blueprints in one call · $${(+sv.cost_usd || 0).toFixed(3)} · ${sv.seconds} s${sv.paid_by ? " · paid by " + esc(sv.paid_by) : ""}</p></div>` : ""}
      <div class="psheets">${proj.sheets.map((s) => `<div class="psheet" data-id="${s.id}">
          <img loading="lazy" src="/bob/lib/api/sheets/${s.id}/thumb" alt=""><div><b>${esc(s.level_name || s.title || s.name)}</b>
          <small>${esc([s.kind, s.title && s.level_name ? s.title : "", s.file_name].filter(Boolean).join(" · "))}</small>
          ${s.superseded_by ? '<span class="flag">REPLACED BY A NEWER REVISION</span>' : ""}<span class="flag opusst" data-sid="${s.id}">OPUS · …</span></div>
          <button class="pbtn" data-open="${s.id}">Open</button></div>`).join("") || '<p class="muted">No blueprints yet: add some.</p>'}</div>
      ${proj.surveyed_at && plans.length ? `<div class="ptable"><b>Per floor</b> <span class="muted small">(answers computed by code, $0; click a number to see it on the blueprint)</span>
        <table id="ptab"><tr><th>Floor</th>${STD.map(([h]) => `<th>${h}</th>`).join("")}</tr>
        ${plans.map((s) => `<tr data-sid="${s.id}"><td><button class="link" data-open="${s.id}">${esc(s.level_name || s.title || s.name)}</button></td>${STD.map((_, j) => `<td data-c="${j}">·</td>`).join("")}</tr>`).join("")}
        <tr class="tot"><td>Whole project</td>${STD.map((_, j) => `<td data-t="${j}"></td>`).join("")}</tr></table></div>` : ""}`;
    $("analyze").onclick = analyze;
    $("opusall").onclick = opusAll;
    opusStatus();
    $("addfiles").onchange = (e) => upload([...e.target.files]);
    const dz = $("pdrop");
    dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("on"); };
    dz.ondragleave = () => dz.classList.remove("on");
    dz.ondrop = (e) => { e.preventDefault(); e.stopPropagation(); dz.classList.remove("on"); upload([...e.dataTransfer.files]); };
    body.querySelectorAll("[data-open]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); openSheet(+b.dataset.open); });
  }
  async function upload(files) {
    if (!files.length) return;
    const pr = $("pprog");
    let done = 0;
    const revs = [];
    for (const f of files) {
      pr.innerHTML = `<span class="spinner"></span>Adding ${esc(f.name)} (${done + 1} of ${files.length})…`;
      try {
        const r = await fetch(`/bob/upload?project=${encodeURIComponent(proj.name)}&name=${encodeURIComponent(f.name)}`, { method: "POST", body: f }).then((x) => x.json());
        if (r.error) throw new Error(r.error);
        if (r.revision_suggestion) revs.push([r, f.name]);
      } catch (e) { pr.innerHTML = `<span class="err">${esc(f.name)}: ${esc(e.message)}</span>`; }
      done++;
    }
    proj = await lib(`/api/projects/${proj.id}`);
    draw();
    $("pprog").innerHTML = `${done} blueprint${done === 1 ? "" : "s"} added. Press <b>Analyze project</b> to have the set read.` +
      revs.map(([r, n]) => `<div class="revnote"><b>${esc(n)}</b> looks like a newer version of <b>${esc(r.revision_suggestion.name)}</b> (${esc(r.revision_suggestion.reason)}).
        <button class="pbtn" data-rev="${r.id}:${r.revision_suggestion.id}">Yes, it replaces it</button></div>`).join("");
    $("pprog").querySelectorAll("[data-rev]").forEach((b) => b.onclick = async () => {
      const [a, o] = b.dataset.rev.split(":"); await post(`/api/sheets/${a}/revision_of/${o}`); b.outerHTML = "<b>saved</b>";
    });
  }
  async function analyze() {
    $("analyze").disabled = true;
    $("pprog").innerHTML = `<span class="spinner"></span>Opus is looking at all ${proj.sheets.length} blueprints together (what each one is, which floor)…`;
    try { await post(`/api/projects/${proj.id}/survey`); }
    catch (e) { $("pprog").innerHTML = `<span class="err">Survey failed: ${esc(e.message)}</span>`; $("analyze").disabled = false; return; }
    proj = await lib(`/api/projects/${proj.id}`);
    draw();
    $("pprog").innerHTML = `<span class="spinner"></span>Reading every floor plan and answering the standard questions (code, $0)…`;
    await fillTable(true);
    $("pprog").textContent = "Analyzed. Open a floor, or ask a question about the whole project.";
  }
  async function fillTable(fresh) {
    const plans = proj.sheets.filter(isPlan), jobs = [];
    for (const s of plans) STD.forEach(([, q], j) => jobs.push([s, j, q]));
    const res = {};
    let k = 0;
    const worker = async () => {
      while (k < jobs.length) {
        const [s, j, q] = jobs[k++];
        const cell = document.querySelector(`#ptab tr[data-sid="${s.id}"] td[data-c="${j}"]`);
        if (cell) cell.innerHTML = '<span class="spinner"></span>';
        try {
          const r = await post("/api/solve", { sheet_id: s.id, question: q });
          res[s.id + ":" + j] = r;
          if (cell) { cell.innerHTML = `<b title="${esc((r.honesty || {}).tier || "")}">${esc(fmt(r.answer, r.unit))}</b>`; cell.dataset.tier = (r.honesty || {}).tier || ""; }
        } catch (e) { if (cell) cell.textContent = "error"; }
        totals(plans, res);
      }
    };
    await Promise.all([worker(), worker(), worker(), worker()]);
  }
  function totals(plans, res) {
    STD.forEach((_, j) => {
      const vals = plans.map((s) => res[s.id + ":" + j]).filter(Boolean);
      const td = document.querySelector(`#ptab td[data-t="${j}"]`);
      if (!td || vals.length < plans.length) return;
      const nums = vals.filter((r) => typeof r.answer === "number"), units = new Set(nums.map((r) => r.unit));
      td.innerHTML = nums.length && units.size === 1 ? `<b>${esc(fmt(nums.reduce((a, r) => a + r.answer, 0), [...units][0]))}</b>${nums.length < vals.length ? ` <small>(${vals.length - nums.length} floor(s) unknown)</small>` : ""}` : "–";
    });
  }
  // Opus reading per floor: status on each card; "read all floors" runs them one after the other
  const localOf = async (s) => (await fetch(`/bob/local?sid=${s.id}&file_name=${encodeURIComponent(s.file_name || "")}`).then((x) => x.json()));
  async function opusStatus() {
    for (const s of proj.sheets) {
      const el = document.querySelector(`.opusst[data-sid="${s.id}"]`);
      if (!el) continue;
      try {
        const l = await localOf(s);
        const r = await fetch("/bob/readstatus?path=" + encodeURIComponent(l.rel || l.path)).then((x) => x.json());
        el.textContent = !r.started ? "OPUS · NOT RUN" : r.reading === "running" ? `OPUS · READING ${Math.round(r.reading_elapsed || 0)} s` :
          r.reading === "done" ? "OPUS · DONE ✓" : r.stage !== "done" ? "OPUS · STARTING" : "OPUS · MODELS ONLY";
        el.classList.toggle("inv", r.reading === "done");
      } catch (e) { el.textContent = "OPUS · ?"; }
    }
  }
  async function opusAll() {
    const b = $("opusall"); b.disabled = true;
    for (const s of proj.sheets) {
      if (!document.body.contains(b)) return;                        // left the project page: stop queueing
      const l = await localOf(s);
      const p = l.rel || l.path;
      const st = await fetch("/bob/readstatus?path=" + encodeURIComponent(p)).then((x) => x.json());
      if (st.started && (st.reading === "done" || st.reading === "running")) continue;
      b.textContent = `Opus reading ${s.level_name || s.title || s.name}…`;
      await fetch("/bob/read?path=" + encodeURIComponent(p)).then((x) => x.json());
      for (;;) {                                                     // one floor at a time
        await new Promise((ok) => setTimeout(ok, 5000));
        opusStatus();
        const r = await fetch("/bob/readstatus?path=" + encodeURIComponent(p)).then((x) => x.json());
        if (r.stage === "error" || (r.stage === "done" && r.reading && r.reading !== "running")) break;
      }
    }
    b.textContent = "Opus: all floors read"; opusStatus();
  }

  async function openSheet(sid) {
    const s = proj.sheets.find((x) => x.id === sid);
    const r = await fetch(`/bob/local?sid=${sid}&file_name=${encodeURIComponent(s.file_name || "")}`).then((x) => x.json());
    if (r.error) { $("pprog").innerHTML = `<span class="err">${esc(r.error)}</span>`; return; }
    home.hidden = true;
    window.bobOpenPath(r.rel || r.path, s.level_name || s.title || s.name);
    scopeBar();
  }
  window.bobOpenProjectSheet = openSheet;
  // open a file in the viewer: a dataset file by its dataset path (the page then uses the exact reader: the building
  // model for BIM plans, the CAD file for Swiss plans), anything else as an uploaded file
  window.bobOpenPath = (p, title) => {
    document.querySelector(".vwin")?.classList.add("vloading");
    if (p && !p.startsWith("/") && typeof openDrawing === "function") {
      return openDrawing({ path: p, title: (title ? title + " · " : "") + p.split("/").pop(), questions: [] });
    }
    const box = $("openpath");
    box.value = p;
    box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  };

  // ------------------------------------------------------------------ ask: this floor, or the whole project
  const asksec = $("q")?.closest("section");
  let scope = "one";
  function scopeBar() {
    if (!asksec || !proj) return;
    let b = $("scopebar");
    if (!b) { $("q").insertAdjacentHTML("afterend", '<div id="scopebar"><button data-s="one" class="on">This floor</button><button data-s="all">Whole project</button></div>'); b = $("scopebar"); }
    b.querySelector('[data-s="all"]').textContent = `Whole project · ${proj.sheets.filter(isPlan).length}`;
    b.querySelectorAll("button").forEach((x) => x.onclick = () => { scope = x.dataset.s; b.querySelectorAll("button").forEach((y) => y.classList.toggle("on", y === x)); });
  }
  $("askbtn")?.addEventListener("click", (e) => {
    if (scope !== "all" || !proj) return;
    e.stopImmediatePropagation(); e.preventDefault();
    askProject($("q").value.trim());
  }, true);
  async function askProject(question) {
    if (!question) return;
    const plans = proj.sheets.filter(isPlan);
    $("answersec").hidden = false;
    const box = $("answer");
    box.innerHTML = `<div class="card"><span class="spinner"></span>Asking all ${plans.length} floors of ${esc(proj.name)}…</div>`;
    const rs = await Promise.all(plans.map((s) => post("/api/solve", { sheet_id: s.id, question }).then((r) => [s, r]).catch((e) => [s, { answer: null, honesty: { tier: "cannot determine", basis: e.message } }])));
    const nums = rs.filter(([, r]) => typeof r.answer === "number"), units = new Set(nums.map(([, r]) => r.unit));
    const total = nums.length && units.size === 1 ? nums.reduce((a, [, r]) => a + r.answer, 0) : null;
    const sv = proj.survey || {};
    box.innerHTML = `<div class="projans"><div class="big">${total != null ? esc(fmt(total, [...units][0])) : "per floor"}</div>
      <p class="muted">${esc(question)} · whole project: ${plans.length} floor plan${plans.length === 1 ? "" : "s"}${total != null && nums.length < rs.length ? ` · ${rs.length - nums.length} floor(s) could not be answered and are not in the total` : ""}</p>
      <table><tr><th>Floor</th><th>Answer</th><th>How</th></tr>${rs.map(([s, r]) => `<tr><td><button class="link" data-open="${s.id}">${esc(s.level_name || s.title || s.name)}</button></td>
        <td><b>${esc(fmt(r.answer, r.unit))}</b></td><td class="muted small">${esc((r.honesty || {}).tier || "")}${(r.library || {}).from_library ? " · from library, $0" : ""}</td></tr>`).join("")}</table>
      ${sv.summary && total == null ? `<p class="muted small"><b>About the project (Opus survey):</b> ${esc(sv.summary)}</p>` : ""}</div>`;
    box.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => openSheet(+b.dataset.open));
  }

  // ------------------------------------------------------------------ the floors of the open project in the Building stack
  window.bobProjectFloors = (path) => {
    if (!proj) return null;
    const name = String(path || "").split(/[\\/]/).pop();
    if (!proj.sheets.some((s) => s.file_name === name)) return null;       // the open file is not part of this project
    const fl = proj.sheets.filter((s) => s.level_order != null && isPlan(s));
    if (fl.length < 2) return null;
    return { building: proj.name, floors: fl.map((s) => ({ n: s.level_order, name: s.level_name || s.title || s.name, sid: s.id, current: s.file_name === name })) };
  };

  // ------------------------------------------------------------------ drag the edge between the blueprint and the panels
  const aside = document.querySelector("aside"), vwin = document.querySelector(".vwin");
  if (main && aside && !$("bobsplit")) {
    aside.insertAdjacentHTML("beforebegin", '<div id="bobsplit" title="Drag to resize"><i></i></div>');
    const sp = $("bobsplit");
    let saved = 0;
    try { saved = +localStorage.getItem("bobAsideW") || 0; } catch (e) { /* private window */ }
    const setW = (w) => { const max = Math.max(320, main.clientWidth - 360); aside.style.width = aside.style.maxWidth = Math.min(max, Math.max(300, w)) + "px"; aside.style.flex = "0 0 auto"; };
    if (saved) setW(saved);
    let drag = null, raf = 0;
    sp.onpointerdown = (e) => { drag = { x: e.clientX, w: aside.getBoundingClientRect().width }; sp.setPointerCapture(e.pointerId); document.body.classList.add("resizing"); };
    sp.onpointermove = (e) => {
      if (!drag) return;
      const w = drag.w + (drag.x - e.clientX);
      cancelAnimationFrame(raf); raf = requestAnimationFrame(() => setW(w));
    };
    sp.onpointerup = () => {
      if (!drag) return;
      drag = null; document.body.classList.remove("resizing");
      try { localStorage.setItem("bobAsideW", Math.round(aside.getBoundingClientRect().width)); } catch (e) { /* */ }
      window.dispatchEvent(new Event("resize"));
    };
    sp.ondblclick = () => { aside.style.width = aside.style.maxWidth = aside.style.flex = ""; try { localStorage.removeItem("bobAsideW"); } catch (e) { /* */ } };
  }

  // ------------------------------------------------------------------ microphone: the browser's own speech recognition, or a clear hint
  const mic = $("micbtn");
  if (mic) {
    mic.hidden = false;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mac = /Mac/.test(navigator.platform), win = /Win/.test(navigator.platform);
    const hint = (why) => {
      let h = $("michint");
      if (!h) { $("q").insertAdjacentHTML("afterend", '<div id="michint"></div>'); h = $("michint"); }
      h.innerHTML = `<b>Voice input is not available here</b>${why ? ` (${esc(why)})` : ""}. Use your computer's own dictation instead:
        ${mac ? "press <kbd>Fn</kbd> twice (or <kbd>🌐</kbd>), or Edit → Start Dictation" : win ? "press <kbd>Win</kbd> + <kbd>H</kbd>" : "your system's dictation"}; on a phone, the microphone on the keyboard.
        It types into the question box. <button class="link" id="michide">OK</button>`;
      $("michide").onclick = () => h.remove();
      $("q").focus();
    };
    let rec = null;
    mic.onclick = () => {
      if (!SR) return hint("this browser has no speech recognition; Chrome and Edge have it");
      if (rec) { rec.stop(); return; }
      rec = new SR(); rec.lang = navigator.language || "en-US"; rec.interimResults = true;
      mic.classList.add("on");
      rec.onresult = (e) => {
        $("q").value = [...e.results].map((r) => r[0].transcript).join(" ");
        if (e.results[e.results.length - 1].isFinal) { rec.stop(); $("askbtn").click(); }
      };
      rec.onerror = (e) => hint({ "not-allowed": "the microphone is blocked for this page", "network": "the browser's speech service could not be reached",
        "service-not-allowed": "this browser does not allow its speech service", "no-speech": "nothing was heard" }[e.error] || e.error);
      rec.onend = () => { mic.classList.remove("on"); rec = null; };
      try { rec.start(); } catch (e) { hint(e.message); }
    };
  }

  // start on the projects screen (not when a deep link opens a file for a demo)
  if (!/open=/.test(location.hash)) showHome();
})();
