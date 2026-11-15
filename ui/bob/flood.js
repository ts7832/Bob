// Page transitions in the One-bit style: the screen fills top to bottom with pixel ink, the page underneath is swapped
// (onCovered), then the ink leaves downwards (onDone). window.bobFlood(variant, onCovered, onDone).
// Variants: drop, rain, blocks, blinds, dither, bricks. Chosen with localStorage "bobFlood" (default rain).
(() => {
  const INK = "#1e1e1e", PAPER = "#fefefe";
  function hatch(g) {
    const p = document.createElement("canvas"); p.width = p.height = 8;
    const pg = p.getContext("2d"); pg.fillStyle = PAPER; pg.fillRect(0, 0, 8, 8); pg.fillStyle = INK;
    for (let i = 0; i < 8; i++) { pg.fillRect(i, 7 - i, 1, 1); pg.fillRect((i + 4) % 8, 7 - i, 1, 1); }
    return g.createPattern(p, "repeat");
  }
  const rnd = (s) => () => (s = (s * 16807) % 2147483647) / 2147483647;     // repeatable noise

  // each variant: (g, W, H, t in 0..1 of the cover phase or the leave phase, leaving) -> draws the ink
  const V = {
    drop(g, W, H, t, out, S) {                       // hatch drops with a ragged pixel edge
      const B = 12, cols = Math.ceil(W / B), rows = Math.ceil(H / B);
      S.jag = S.jag || Array.from({ length: cols }, (_, c) => (Math.sin(c * 1.7) + Math.sin(c * 0.53) + 2) * 1.6 + (c % 3));
      g.fillStyle = S.pat;
      for (let c = 0; c < cols; c++) { const r = Math.max(0, Math.floor(t * (rows + 8) - S.jag[c])); out ? g.fillRect(c * B, r * B, B, H) : g.fillRect(c * B, 0, B, r * B); }
    },
    rain(g, W, H, t, out, S) {                       // pixel rain: streaks fall at their own speeds and pile up
      const B = 8, cols = Math.ceil(W / B), rows = Math.ceil(H / B);
      if (!S.drops) { const r = rnd(7); S.drops = Array.from({ length: cols }, () => ({ v: 0.55 + r() * 0.9, d: r() * 0.35, len: 3 + Math.floor(r() * 7) })); }
      for (let c = 0; c < cols; c++) {
        const k = S.drops[c], f = Math.max(0, (t - k.d) / (1 - k.d)) * k.v * 1.25, head = Math.floor(f * (rows + k.len));
        if (!out) {
          g.fillStyle = S.pat; g.fillRect(c * B, 0, B, Math.max(0, head - k.len) * B);          // what has fallen so far
          g.fillStyle = INK; for (let j = Math.max(0, head - k.len); j < head; j += 2) g.fillRect(c * B + 2, j * B, B - 4, B - 2);   // the falling streak
        } else {
          const top = Math.floor(f * (rows + k.len));
          g.fillStyle = S.pat; g.fillRect(c * B, top * B, B, H);
          g.fillStyle = INK; for (let j = top - k.len; j < top; j += 2) if (j >= 0) g.fillRect(c * B + 2, j * B, B - 4, B - 2);
        }
      }
    },
    blocks(g, W, H, t, out, S) {                     // blocks fall and stack (rows fill from the bottom up), then fall away
      const B = 32, cols = Math.ceil(W / B), rows = Math.ceil(H / B);
      if (!S.order) { const r = rnd(3); S.order = []; for (let y = rows - 1; y >= 0; y--) for (let c = 0; c < cols; c++) S.order.push({ c, y, d: r() * 0.6 }); S.order.sort((a, b) => b.y - a.y || a.d - b.d); }
      const n = S.order.length;
      S.order.forEach((b, i) => {
        const start = (i / n) * 0.8, f = Math.min(1, Math.max(0, (t - start) / 0.2));
        if (!out) { if (f <= 0) return; const y = (b.y + 1) * B * f - B; g.fillStyle = S.pat; g.fillRect(b.c * B, Math.min(y, b.y * B), B - 1, B - 1); g.strokeStyle = INK; g.strokeRect(b.c * B + .5, Math.min(y, b.y * B) + .5, B - 2, B - 2); }
        else { const y = b.y * B + f * f * H; if (y > H) return; g.fillStyle = S.pat; g.fillRect(b.c * B, y, B - 1, B - 1); g.strokeStyle = INK; g.strokeRect(b.c * B + .5, y + .5, B - 2, B - 2); }
      });
    },
    blinds(g, W, H, t, out, S) {                     // staggered hatched bands drop like blinds, pixel edges
      const BAND = 48, n = Math.ceil(H / BAND), B = 8;
      for (let i = 0; i < n; i++) {
        const f = Math.min(1, Math.max(0, t * 1.6 - i / n * 0.6));
        const h = Math.ceil(f * BAND / B) * B;
        g.fillStyle = S.pat;
        out ? g.fillRect(0, i * BAND + h, W, BAND - h) : g.fillRect(0, i * BAND, W, h);
        if (f > 0 && f < 1) { g.fillStyle = INK; g.fillRect(0, i * BAND + h - (out ? 0 : 3), W, 3); }
      }
    },
    dither(g, W, H, t, out, S) {                     // Bayer dissolve, darker towards the falling front
      const B = 6, cols = Math.ceil(W / B), rows = Math.ceil(H / B);
      const M = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
      g.fillStyle = INK;
      for (let y = 0; y < rows; y++) {
        const lvl = Math.max(0, Math.min(1, t * 1.8 - (y / rows) * 0.8));
        const v = out ? 1 - lvl : lvl;
        if (v <= 0) continue;
        for (let x = 0; x < cols; x++) if (M[(y % 4) * 4 + (x % 4)] / 16 < v) g.fillRect(x * B, y * B, B, B);
      }
    },
    bricks(g, W, H, t, out, S) {                     // a wall is built row by row from the top, then knocked down
      const BW = 48, BH = 20, rows = Math.ceil(H / BH), cols = Math.ceil(W / BW) + 1;
      if (!S.fall) { const r = rnd(11); S.fall = Array.from({ length: rows * cols }, () => r()); }
      for (let y = 0; y < rows; y++) for (let c = 0; c < cols; c++) {
        const i = y * cols + c, off = y % 2 ? -BW / 2 : 0;
        let x = c * BW + off, yy = y * BH;
        if (!out) { const lay = (y * cols + c) / (rows * cols); if (t < lay) continue; }
        else { const d = t * 1.4 - (1 - y / rows) * 0.4 - S.fall[i] * 0.2; if (d > 0) yy += d * d * H * 1.6; if (yy > H) continue; }
        g.fillStyle = S.pat; g.fillRect(x + 1, yy + 1, BW - 2, BH - 2);
        g.strokeStyle = INK; g.lineWidth = 2; g.strokeRect(x + 1, yy + 1, BW - 2, BH - 2);
      }
    },
  };

  window.bobFloodVariants = Object.keys(V);
  window.bobFlood = (variant, onCovered, onDone) => {
    const draw = V[variant] || V.rain;
    const cv = document.createElement("canvas");
    cv.style.cssText = "position:fixed;inset:0;width:100vw;height:100vh;z-index:100;pointer-events:none;image-rendering:pixelated";
    document.body.appendChild(cv);
    const W = cv.width = innerWidth, H = cv.height = innerHeight, g = cv.getContext("2d");
    const S = { pat: hatch(g) }, T1 = variant === "blocks" || variant === "bricks" ? 900 : 700, T2 = variant === "bricks" ? 900 : 650;
    const t0 = performance.now();
    let covered = false;
    const step = (now) => {
      const t = now - t0;
      g.clearRect(0, 0, W, H);
      if (t < T1) draw(g, W, H, t / T1, false, S);
      else {
        if (!covered) { covered = true; g.fillStyle = S.pat; g.fillRect(0, 0, W, H); onCovered && onCovered(); }
        draw(g, W, H, Math.min(1, (t - T1) / T2), true, S);
        if (t > T1 + T2) { cv.remove(); onDone && onDone(); return; }
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
})();
