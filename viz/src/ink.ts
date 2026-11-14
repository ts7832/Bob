// Symbol-accurate evidence: the drawn symbol itself glows (door leaf and swing, WC bowl, window lines), not just a box.
// Raster sheets (scans, PDF pages, our Swiss DXF raster): dark thin strokes inside the evidence box, with thick wall
// poché removed; doors also take the thin strokes connected to their opening, because the leaf and swing lie outside
// the detector box / opening polygon. BIM sheets draw the element's own SVG paths. Display only: never counted.
import type { Drawing, Element, Pt } from './types'
import { bbox, inside } from './geom'

export type Cls = 'door' | 'window' | 'wc' | 'sink' | 'bathtub' | 'shower' | 'stairs' | 'column' | 'stove' | 'kitchen' | 'fixture'
  | 'room' | 'apartment' | 'floor' | 'wall' | 'other'

// Backend / IFC / detector class names -> one small vocabulary (glyphs, chips, symbol style).
export function normCls(raw?: string | null): Cls | null {
  if (!raw) return null
  const s = raw.toLowerCase().replace(/^ifc/, '')
  if (/door|^arc$/.test(s)) return 'door' // arcN: doors found from their swing arcs
  if (/^win|window/.test(s)) return 'window'
  if (/^wc$|toilet/.test(s)) return 'wc'
  if (/sink|basin|lavabo/.test(s)) return 'sink'
  if (/bath|tub/.test(s)) return 'bathtub'
  if (/shower/.test(s)) return 'shower'
  if (/stair/.test(s)) return 'stairs'
  if (/column/.test(s)) return 'column'
  if (/stove/.test(s)) return 'stove'
  if (/kitchen_unit/.test(s)) return 'kitchen'
  if (/sanitary|furnish|furniture|fixture|bench|^fx$|terminal/.test(s)) return 'fixture'
  if (/^unit$|apartment/.test(s)) return 'apartment'
  if (/^floor$|storey/.test(s)) return 'floor'
  if (/wall|railing/.test(s)) return 'wall'
  if (/space|room|bed|bath|balcony|living|dining|entry|storage|utility|dressing|terrace|garage|kitchen|sauna/.test(s)) return 'room'
  return 'other'
}

// Symbols drawn as linework: these glow. Rooms, units, floors and walls keep the filled-area style.
export const SYMBOL = new Set<Cls>(['door', 'window', 'wc', 'sink', 'bathtub', 'shower', 'stairs', 'column', 'stove', 'kitchen', 'fixture'])

export const CLS_NAME: Record<Cls, [string, string]> = {
  door: ['door', 'doors'], window: ['window', 'windows'], wc: ['WC', 'WCs'], sink: ['sink', 'sinks'], bathtub: ['bathtub', 'bathtubs'],
  shower: ['shower', 'showers'], stairs: ['stair', 'stairs'], column: ['column', 'columns'], stove: ['stove', 'stoves'],
  kitchen: ['kitchen unit', 'kitchen units'], fixture: ['fixture', 'fixtures'], room: ['room', 'rooms'],
  apartment: ['apartment', 'apartments'], floor: ['floor', 'floors'], wall: ['wall', 'walls'], other: ['item', 'items'],
}

// 16x16 plan-symbol glyphs (stroke = currentColor), drawn like the symbols on a plan.
export const GLYPH: Partial<Record<Cls, string>> = {
  door: 'M1.5 14.5h2.5M15 14.5h-1M4 14.5V3.5M4 3.5a11 11 0 0 1 10 11',
  window: 'M1 6h14M1 10h14M1 6v4M15 6v4M8 6v4',
  wc: 'M4 1.5h8v3H4zM4.5 9.5a3.5 4.6 0 1 0 7 0a3.5 4.6 0 1 0 -7 0',
  sink: 'M2 3.5h12v9H2zM5.5 8a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0',
  bathtub: 'M1 3.5h14v9H1zM3 5.5h10v5H3z',
  shower: 'M2 2h12v12H2zM2 2l12 12M14 2L2 14',
  stairs: 'M1.5 14.5h3v-3h3v-3h3v-3h3v-3h1.5',
  column: 'M4 4h8v8H4zM4 4l8 8M12 4l-8 8',
  stove: 'M2 2.5h12v11H2zM4.5 8a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0 -7 0',
  kitchen: 'M1 4h14v8H1zM3.5 8a1.6 1.6 0 1 0 3.2 0a1.6 1.6 0 1 0 -3.2 0M9.3 8a1.6 1.6 0 1 0 3.2 0a1.6 1.6 0 1 0 -3.2 0',
  fixture: 'M3 3h10v10H3zM6 8h4',
}

export interface Ink { canvas: HTMLCanvasElement; x0: number; y0: number; x1: number; y1: number } // sheet px

const WORK = 640 // long side of the working crop, px
const SPRITE = 320 // the glow sprite is upscaled to at least this long side, so small scans stay smooth when zoomed

export async function inkOf(d: Drawing, el: Element, cls: Cls, color: string): Promise<Ink | null> {
  if (el.geometry === 'polyline' || el.polygon.length < 3) return null
  // doors: follow the leaf and swing only from a thin opening box (a box that already holds the swing is the whole door)
  const b = bbox(el.polygon), thin = Math.max(b.w, b.h) > 1.8 * Math.max(Math.min(b.w, b.h), 1e-6)
  const r = el.markup ? await fromMarkup(d, el, color) : fromRaster(d, el, cls === 'door' && thin, color)
  if (import.meta.env.DEV && r) ((window as any).__dvInk ??= {})[el.id] = r.canvas // tests: inspect the sprites
  return r
}

// ---- BIM: the element's own SVG paths, recoloured (exact)
async function fromMarkup(d: Drawing, el: Element, color: string): Promise<Ink | null> {
  const b = bbox(el.ext ?? el.polygon)
  const L = Math.max(b.w, b.h, 4), pad = 0.18 * L
  const R = { x0: b.x0 - pad, y0: b.y0 - pad, x1: b.x1 + pad, y1: b.y1 + pad }
  const s = SPRITE / Math.max(R.x1 - R.x0, R.y1 - R.y0) // sprite px per sheet px
  const W = Math.ceil((R.x1 - R.x0) * s), H = Math.ceil((R.y1 - R.y0) * s)
  const [ux0, uy0] = d.fromPx([R.x0, R.y0]), [ux1, uy1] = d.fromPx([R.x1, R.y1])
  const upp = (ux1 - ux0) / W // svg units per sprite px
  const css = `*{stroke:${color}!important;stroke-width:${Math.max(3 * upp, 0.2).toFixed(5)}px!important;fill:none!important;stroke-linecap:round;stroke-linejoin:round}`
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${ux0} ${uy0} ${ux1 - ux0} ${uy1 - uy0}" width="${W}" height="${H}"><style>${css}</style>${el.markup}</svg>`
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = url })
    const core = document.createElement('canvas')
    core.width = W; core.height = H
    core.getContext('2d')!.drawImage(img, 0, 0, W, H)
    return glow(core, R, s, color)
  } finally {
    URL.revokeObjectURL(url)
  }
}

// ---- Raster: thin dark strokes in the box (+ connected ones for doors), from the drawing at full resolution
function fromRaster(d: Drawing, el: Element, grow: boolean, color: string): Ink | null {
  const src = d.source
  const sw = src instanceof HTMLImageElement ? src.naturalWidth : src.width
  const s0 = sw / d.width // source px per sheet px
  const b = bbox(el.polygon), L = Math.max(b.w, b.h, 2)
  const pad = grow ? 1.1 * L : 0.06 * L + 2 / s0
  const R = { x0: Math.max(0, b.x0 - pad), y0: Math.max(0, b.y0 - pad), x1: Math.min(d.width, b.x1 + pad), y1: Math.min(d.height, b.y1 + pad) }
  const cw = (R.x1 - R.x0) * s0, ch = (R.y1 - R.y0) * s0
  if (cw < 2 || ch < 2) return null
  const f = Math.min(1, WORK / Math.max(cw, ch)) // working px per source px
  const w = Math.max(2, Math.round(cw * f)), h = Math.max(2, Math.round(ch * f))
  const c = document.createElement('canvas')
  c.width = w; c.height = h
  const g = c.getContext('2d', { willReadFrequently: true })!
  g.fillStyle = '#fff'; g.fillRect(0, 0, w, h)
  g.drawImage(src, R.x0 * s0, R.y0 * s0, cw, ch, 0, 0, w, h)
  let px: Uint8ClampedArray
  try { px = g.getImageData(0, 0, w, h).data } catch { return null } // cross-origin image: keep the box style
  const n = w * h, lum = new Float32Array(n), sat = new Uint8Array(n)
  for (let i = 0; i < n; i++) {
    const r = px[4 * i], gg = px[4 * i + 1], bb = px[4 * i + 2]
    lum[i] = 0.299 * r + 0.587 * gg + 0.114 * bb
    sat[i] = Math.max(r, gg, bb) - Math.min(r, gg, bb) > 70 ? (r > gg + 60 && r > bb + 60 ? 2 : 1) : 0 // 2 = red (unit outlines, mark-ups)
  }
  // paper = a high percentile of the crop (coloured room fills stay paper); ink = clearly darker than paper
  const sorted = Float32Array.from(lum).sort()
  const paper = sorted[Math.floor(0.9 * (n - 1))]
  const thr = Math.min(paper - 55, 190)
  const ink = new Uint8Array(n)
  for (let i = 0; i < n; i++) ink[i] = lum[i] < thr ? 1 : 0
  // remove thick strokes (wall poché): opening at hairline + 2, slightly widened to swallow its anti-aliased edges
  const k = Math.min(15, Math.max(3, hairline(ink, w, h) + 2))
  // (walls are grey or black: coloured lines, e.g. a 4 px unit outline, never count as wall)
  const grey = new Uint8Array(n)
  for (let i = 0; i < n; i++) grey[i] = ink[i] && !sat[i] ? 1 : 0
  const opened = dilate(erode(grey, w, h, k), w, h, k), thick = dilate(opened, w, h, 3)
  const thin = new Uint8Array(n), pass = new Uint8Array(n) // pass: may be walked through (hinges touch the wall), not drawn
  for (let i = 0; i < n; i++) { thin[i] = ink[i] && !thick[i] ? 1 : 0; pass[i] = ink[i] && !opened[i] ? 1 : 0 }
  // seeds: thin ink inside the evidence outline (boxes get a 2 px tolerance)
  const k2 = s0 * f, tol = 2
  const poly = el.polygon.map(([x, y]) => [(x - R.x0) * k2, (y - R.y0) * k2] as Pt)
  const pb = bbox(poly), isBox = el.geometry === 'box'
  const keep = new Uint8Array(n)
  const stack: number[] = []
  let count = 0
  for (let y = Math.max(0, Math.floor(pb.y0 - tol)); y < Math.min(h, Math.ceil(pb.y1 + tol)); y++)
    for (let x = Math.max(0, Math.floor(pb.x0 - tol)); x < Math.min(w, Math.ceil(pb.x1 + tol)); x++) {
      const i = y * w + x
      if (!thin[i] || sat[i] === 2 || (!isBox && !inside([x + 0.5, y + 0.5], poly))) continue
      keep[i] = 1; count++; stack.push(i)
    }
  if (count < 6) return null // nothing drawn as linework here: keep the plain box style
  // doors: follow the thin strokes connected to the opening (leaf, swing), and only across the wall: within the opening's span along its long side (lines running on along the wall are not the door)
  if (grow) {
    const along = pb.x1 - pb.x0 >= pb.y1 - pb.y0, t = 0.1 * Math.max(pb.x1 - pb.x0, pb.y1 - pb.y0)
    const lo = along ? pb.x0 - t : pb.y0 - t, hi = along ? pb.x1 + t : pb.y1 + t
    const seen = Uint8Array.from(keep)
    while (stack.length) {
      const i = stack.pop()!, x = i % w, y = (i / w) | 0
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const X = x + dx, Y = y + dy
        if (X < 0 || Y < 0 || X >= w || Y >= h) continue
        const a = along ? X : Y
        if (a < lo || a > hi) continue
        const j = Y * w + X
        if (seen[j] || !pass[j]) continue
        seen[j] = 1; stack.push(j)
        if (thin[j] && !sat[j]) keep[j] = 1 // coloured lines crossing the swing (unit outlines) are walked over, not drawn
      }
    }
  }
  // grown far beyond the evidence box: it followed other linework (dimension lines, walls), keep only what is in the box
  if (grow) {
    let gx0 = w, gy0 = h, gx1 = -1, gy1 = -1
    for (let i = 0; i < n; i++) if (keep[i]) { const x = i % w, y = (i / w) | 0; if (x < gx0) gx0 = x; if (x > gx1) gx1 = x; if (y < gy0) gy0 = y; if (y > gy1) gy1 = y }
    const L2 = Math.max(pb.x1 - pb.x0, pb.y1 - pb.y0)
    if ((gx1 - gx0) * (gy1 - gy0) > 2.2 * L2 * L2) {
      keep.fill(0)
      for (let y = Math.max(0, Math.floor(pb.y0 - tol)); y < Math.min(h, Math.ceil(pb.y1 + tol)); y++)
        for (let x = Math.max(0, Math.floor(pb.x0 - tol)); x < Math.min(w, Math.ceil(pb.x1 + tol)); x++) if (thin[y * w + x] && sat[y * w + x] !== 2) keep[y * w + x] = 1
    }
  }
  // core: the kept strokes in the answer colour, as thick as drawn (the halo makes thin ones readable when zoomed out),
  // cropped to where they are (so the sprite's extent is the symbol's extent: the tag goes on the free side)
  let x0 = w, y0 = h, x1 = -1, y1 = -1
  for (let i = 0; i < n; i++) if (keep[i]) { const x = i % w, y = (i / w) | 0; if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y }
  x0 = Math.max(0, x0 - 1); y0 = Math.max(0, y0 - 1); x1 = Math.min(w - 1, x1 + 1); y1 = Math.min(h - 1, y1 + 1)
  const mw = x1 - x0 + 1, mh = y1 - y0 + 1
  const m = document.createElement('canvas')
  m.width = mw; m.height = mh
  const mg = m.getContext('2d')!, id = mg.createImageData(mw, mh)
  const [cr, cg, cb] = rgb(color)
  for (let y = 0; y < mh; y++) for (let x = 0; x < mw; x++) {
    if (!keep[(y + y0) * w + x + x0]) continue
    const o = 4 * (y * mw + x)
    id.data[o] = cr; id.data[o + 1] = cg; id.data[o + 2] = cb; id.data[o + 3] = 255
  }
  mg.putImageData(id, 0, 0)
  const up = Math.max(1, Math.min(6, SPRITE / Math.max(mw, mh)))
  const core = document.createElement('canvas')
  core.width = Math.round(mw * up); core.height = Math.round(mh * up)
  const cg2 = core.getContext('2d')!
  cg2.imageSmoothingEnabled = true; cg2.imageSmoothingQuality = 'high'
  cg2.drawImage(m, 0, 0, core.width, core.height)
  const sp = 1 / k2 // sheet px per working px
  const K = { x0: R.x0 + x0 * sp, y0: R.y0 + y0 * sp, x1: R.x0 + (x1 + 1) * sp, y1: R.y0 + (y1 + 1) * sp }
  return glow(core, K, core.width / (K.x1 - K.x0), color)
}

// The whole object lights up: the area its strokes enclose (a WC's bowl and tank, a door's swing) filled in the answer
// colour, the strokes on top, and a soft halo around it. The canvas grows by the halo so it is never clipped.
function glow(core: HTMLCanvasElement, R: { x0: number; y0: number; x1: number; y1: number }, s: number, color: string): Ink {
  const blur = Math.max(5, 0.05 * Math.max(core.width, core.height)), M = Math.ceil(blur * 2)
  const c = document.createElement('canvas')
  c.width = core.width + 2 * M; c.height = core.height + 2 * M
  const g = c.getContext('2d')!
  const body = fillOf(core, color) ?? core
  g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high'
  g.shadowColor = color; g.shadowBlur = blur
  g.globalAlpha = 0.45
  g.drawImage(body, M, M, core.width, core.height) // halo around the whole object
  g.shadowBlur = 0
  if (body !== core) { g.globalAlpha = 0.26; g.drawImage(body, M, M, core.width, core.height) } // the object's area
  g.globalAlpha = 1
  g.drawImage(core, M, M) // its strokes, crisp
  const e = M / s
  return { canvas: c, x0: R.x0 - e, y0: R.y0 - e, x1: R.x1 + e, y1: R.y1 + e }
}

// The object's whole footprint: the convex hull of its strokes (a door's swing quarter, a WC's tank and bowl), as a
// canvas in the answer colour.
function fillOf(core: HTMLCanvasElement, color: string): HTMLCanvasElement | null {
  const f = Math.min(1, 200 / Math.max(core.width, core.height))
  const w = Math.max(2, Math.round(core.width * f)), h = Math.max(2, Math.round(core.height * f))
  const c = document.createElement('canvas')
  c.width = w; c.height = h
  const g = c.getContext('2d', { willReadFrequently: true })!
  g.drawImage(core, 0, 0, w, h)
  const a = g.getImageData(0, 0, w, h).data
  const pts: Pt[] = [] // per row, the outermost stroke pixels are enough for the hull
  for (let y = 0; y < h; y++) {
    let l = -1, r = -1
    for (let x = 0; x < w; x++) if (a[4 * (y * w + x) + 3] > 30) { if (l < 0) l = x; r = x }
    if (l >= 0) pts.push([l, y], [r + 1, y], [l, y + 1], [r + 1, y + 1])
  }
  const hull = convexHull(pts)
  if (hull.length < 3) return null
  g.clearRect(0, 0, w, h)
  g.beginPath(); hull.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath()
  g.fillStyle = color; g.fill()
  return c
}

function convexHull(p: Pt[]): Pt[] { // monotone chain
  const q = [...p].sort((a, b) => a[0] - b[0] || a[1] - b[1])
  if (q.length < 3) return q
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
  const lo: Pt[] = [], hi: Pt[] = []
  for (const v of q) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], v) <= 0) lo.pop(); lo.push(v) }
  for (let i = q.length - 1; i >= 0; i--) { const v = q[i]; while (hi.length >= 2 && cross(hi[hi.length - 2], hi[hi.length - 1], v) <= 0) hi.pop(); hi.push(v) }
  return [...lo.slice(0, -1), ...hi.slice(0, -1)]
}

function rgb(hex: string): [number, number, number] {
  const v = parseInt(hex.replace('#', '').slice(0, 6), 16)
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255]
}

// Most common dark run length (1-8 px): the width of the crop's hairlines.
function hairline(m: Uint8Array, w: number, h: number) {
  const cnt = new Array(10).fill(0)
  const add = (r: number) => { if (r >= 1 && r <= 8) cnt[r]++ }
  for (let y = 0; y < h; y++) { let r = 0; for (let x = 0; x <= w; x++) { if (x < w && m[y * w + x]) r++; else { add(r); r = 0 } } }
  for (let x = 0; x < w; x++) { let r = 0; for (let y = 0; y <= h; y++) { if (y < h && m[y * w + x]) r++; else { add(r); r = 0 } } }
  let best = 1
  for (let r = 1; r <= 8; r++) if (cnt[r] > cnt[best]) best = r
  return best
}

// k x k square erosion / dilation, separable (rows then columns)
function erode(m: Uint8Array, w: number, h: number, k: number) { return morph(m, w, h, k, true) }
function dilate(m: Uint8Array, w: number, h: number, k: number) { return morph(m, w, h, k, false) }
function morph(m: Uint8Array, w: number, h: number, k: number, ero: boolean) {
  const r0 = Math.floor((k - 1) / 2), r1 = k - 1 - r0
  const pass = (src: Uint8Array, horiz: boolean) => {
    const out = new Uint8Array(w * h)
    const len = horiz ? w : h, lines = horiz ? h : w
    for (let l = 0; l < lines; l++) {
      const at = (t: number) => (horiz ? l * w + t : t * w + l)
      for (let t = 0; t < len; t++) {
        let v = ero ? 1 : 0
        for (let q = t - r0; q <= t + r1; q++) {
          const s = src[at(q < 0 ? 0 : q >= len ? len - 1 : q)] // edges repeat: no false erosion at the crop border
          if (ero ? !s : s) { v = ero ? 0 : 1; break }
        }
        out[at(t)] = v
      }
    }
    return out
  }
  return pass(pass(m, true), false)
}
