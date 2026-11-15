// Bob layer on top of kokoro's page: One-bit window hooks, the QR button for the phone, and the audit trail of every
// answer. Loaded by ui/index.html; served only by library/team_server.py (at /bob/onebit.js).
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const LIBRARY = localStorage.getItem("bobLibrary") || "https://oliver-universityapps--bob-library-web.us-east.modal.run";

  // ---- wording: blueprint, not drawing (static labels only; answers and data are left alone)
  const WORDS = [["Open a drawing", "Open a blueprint"], ["Drawing", "Blueprint"], ["Drop a drawing here", "Drop a blueprint here"],
    ["Asked on this drawing", "Asked on this blueprint"], ["What it found on this drawing", "What it found on this blueprint"]];
  const reword = (el) => { for (const [a, b] of WORDS) if (el.textContent.trim() === a) el.textContent = b; };

  // ---- window title bars: every aside heading gets its label in a span (the striped bar shows round it)
  const dress = () => {
    document.querySelectorAll("aside section > h2").forEach((h) => {
      if (h.firstElementChild && h.firstElementChild.tagName === "SPAN" && h.childNodes.length === 1) return;
      reword(h);
      h.innerHTML = `<span>${esc(h.textContent)}</span>`;
    });
    document.querySelectorAll(".drop b, #title").forEach(reword);
  };

  // ---- the blueprint view inside a window whose title follows the open file
  const viewer = $("viewer");
  if (viewer && !viewer.parentElement.classList.contains("vwin")) {
    const w = document.createElement("div");
    w.className = "vwin";
    w.innerHTML = '<div class="tb"><span id="vtitle">Blueprint</span><i id="vmeta">plan view</i></div>';
    viewer.parentElement.insertBefore(w, viewer);
    w.appendChild(viewer);
  }
  const title = $("title");
  if (title) {
    const sync = () => { $("vtitle").textContent = title.textContent; };
    new MutationObserver(sync).observe(title, { childList: true, characterData: true, subtree: true });
    sync();
  }

  // ---- the Question window: heavy black frame while in use
  const q = $("q"), asksec = q && q.closest("section");
  if (asksec) {
    asksec.id = asksec.id || "asksec";
    const on = (x) => asksec.classList.toggle("on", x);
    document.addEventListener("pointerdown", (e) => on(asksec.contains(e.target)));
    asksec.addEventListener("focusin", () => on(true));
  }

  // ---- QR button: scan a blueprint with your phone
  const header = document.querySelector("header");
  if (header && !$("qrbtn")) {
    header.insertAdjacentHTML("beforeend", `<button id="qrbtn" title="Scan a blueprint with your phone" aria-label="Scan with phone">
      <svg viewBox="0 0 24 24"><path d="M3 3h8v8H3V3zm2 2v4h4V5H5zm8-2h8v8h-8V3zm2 2v4h4V5h-4zM3 13h8v8H3v-8zm2 2v4h4v-4H5zm8-2h2v2h-2v-2zm4 0h4v2h-2v2h-2v-4zm-4 4h2v2h2v2h-4v-4zm6 2h2v2h-2v-2z"/></svg></button>`);
    document.body.insertAdjacentHTML("beforeend", `<div id="qrveil"><div id="qrcard"><img id="qrimg" alt="QR code for the phone page">
      <b>Scan a blueprint with your phone</b><small id="qrurl"></small></div></div>`);
    const btn = $("qrbtn"), veil = $("qrveil"), card = $("qrcard");
    const place = () => {
      const b = btn.getBoundingClientRect(), c = card.getBoundingClientRect();
      return `translate(${b.left + b.width / 2 - (c.left + c.width / 2)}px, ${b.top + b.height / 2 - (c.top + c.height / 2)}px) scale(${b.width / c.width})`;
    };
    btn.onclick = () => {
      $("qrimg").src = LIBRARY + "/api/qr.svg"; $("qrurl").textContent = LIBRARY + "/phone";
      card.style.transition = "none"; card.style.transform = "none"; card.style.transform = place();
      card.getBoundingClientRect(); card.style.transition = ""; veil.classList.add("on"); card.style.transform = "none";
    };
    const close = () => { card.style.transform = place(); veil.classList.remove("on"); };
    veil.onclick = (e) => { if (e.target === veil) close(); };
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && veil.classList.contains("on")) close(); });
  }

  // ---- audit trail: every /api/ask answer shows how it was produced, step by step
  const answersec = $("answersec");
  if (answersec && !$("auditsec")) {
    answersec.insertAdjacentHTML("afterend", '<section id="auditsec" hidden><h2>How this answer was made</h2><div id="audit"></div></section>');
  }
  const render = (r) => {
    const a = r && r.audit, box = $("audit");
    if (!box) return;
    if (!a) { $("auditsec").hidden = true; return; }
    const lib = r.library || {};
    const flags = [];
    flags.push(lib.from_library ? '<span class="flag inv">FROM LIBRARY · $0</span>' : '<span class="flag">NEW · ENGINE ON MODAL</span>');
    if (a.where) flags.push(`<span class="flag">${esc(a.where)}</span>`);
    flags.push(a.language_model_used ? '<span class="flag inv">LANGUAGE MODEL USED</span>' : '<span class="flag">NO LANGUAGE MODEL</span>');
    if (lib.parts_from_library) flags.push(`<span class="flag inv">${lib.parts_from_library} PART(S) FROM LIBRARY</span>`);
    const steps = (a.steps || []).map((s, i) => `<div class="step${/^language model/i.test(s.actor || "") ? " llm" : ""}">
        <span class="n">${i + 1}</span><b>${esc(s.stage)}</b><span></span><span class="who">${esc(s.actor)}</span>
        ${s.detail ? `<span class="why">${esc(s.detail)}</span>` : ""}
        ${s.confidence != null ? `<span class="conf">CONFIDENCE ${Math.round(s.confidence * 100)}%</span>` : ""}</div>`).join("");
    const parts = (r.parts || []).map((p, i) => `<div><b>Part ${i + 1}</b> · ${esc(p.question)} → <b>${esc(p.answer ?? "cannot determine")}</b>
        ${p.unit && p.unit !== "count" ? esc(p.unit) : ""} · ${esc(p.tier || "")} · ${p.from_library ? "from library" : `engine ${(+p.seconds || 0).toFixed(2)} s`}</div>`).join("");
    box.innerHTML = `<div class="flags">${flags.join("")}</div>` + (parts ? `<div class="parts">${parts}</div>` : "") +
      `<div class="steps">${steps}</div>` + (a.reused ? `<p class="muted" style="font:12px var(--mono);margin-top:8px">${esc(a.reused)}</p>` : "");
    $("auditsec").hidden = false;
    dress();
  };
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const res = await realFetch(input, init);
    try {
      const url = typeof input === "string" ? input : input.url;
      if (url.includes("/api/ask")) res.clone().json().then(render).catch(() => {});
    } catch (e) { /* never break the page */ }
    return res;
  };

  // ---- room chips in English: the printed name stays beside it (it is the evidence); one translation per name, stored
  const PW = localStorage.getItem("bobPassword") || "123456789";
  const named = {};
  const english = async () => {
    const chips = [...document.querySelectorAll('#understood .fchip[data-found^="room:"]')].filter((c) => !c.dataset.en);
    if (!chips.length) return;
    const names = chips.map((c) => c.dataset.found.slice(5)).filter((n) => !(n in named));
    if (names.length) {
      try {
        const r = await fetch(LIBRARY + "/api/names", { method: "POST", headers: { "x-bob-password": PW, "content-type": "application/json" },
          body: JSON.stringify({ names }) }).then((x) => x.json());
        Object.assign(named, r);
      } catch (e) { return; }
    }
    for (const c of chips) {
      const orig = c.dataset.found.slice(5), t = named[orig];
      c.dataset.en = "1";
      if (!t || t.english === orig || t.source === "unchanged") continue;
      const n = c.querySelector("b") ? c.querySelector("b").outerHTML : "";
      c.innerHTML = `${n} ${esc(t.english)} <span class="orig">${esc(orig)}</span>`;
      c.title = `printed on the blueprint as "${orig}"; English ${t.source === "words" ? "from the word list" : "guessed by a language model"}`;
      if (t.source !== "words") c.classList.add("guess");
    }
  };
  const und = $("understood");
  if (und) new MutationObserver(() => setTimeout(english, 50)).observe(und, { childList: true, subtree: true });

  // deep links for demos and checks: /#open=<file path>&ask=<question>  (both optional). Waits for the page's own first
  // open (it opens the first checked drawing when its list arrives), then opens the file, then asks once it is open.
  const hp = new URLSearchParams(location.hash.slice(1));
  const until = (ok, then, tries = 600) => { if (ok()) then(); else if (tries > 0) setTimeout(() => until(ok, then, tries - 1), 250); };
  const cur = () => { try { return typeof current !== "undefined" ? current : null; } catch (e) { return null; } };
  const want = hp.get("open");
  const askNow = () => { if (hp.get("ask") && q && $("askbtn")) { q.value = hp.get("ask"); $("askbtn").click(); } };
  if (want && $("openpath")) until(() => cur(), () => {
    const box = $("openpath");
    box.value = want;
    box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    const base = want.split(/[\\/]/).pop();
    until(() => cur() && String(cur().path).split(/[\\/]/).pop() === base, () => setTimeout(askNow, 1500));
  });
  else if (hp.get("ask")) until(() => cur(), () => setTimeout(askNow, 1500));

  dress();
  new MutationObserver(dress).observe(document.querySelector("aside") || document.body, { childList: true, subtree: true });
})();
