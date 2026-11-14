// Several drawings on one sheet (two unit plans, a plan plus elevations, a site plan plus perspectives): where each is,
// with its printed title and scale. Found from the page itself (clusters of ink once the border and grid lines are
// removed), titled from the backend's own texts and scale notes; on scans, the detector's floors are the drawings.
// Used to say which drawing an answer comes from and to warn when its evidence spans several. Display only.
import type { Drawing, Element, Pt, SheetJSON, SheetView } from './types'

const KIND: [string, RegExp][] = [
  ['plan', /\b(plan|floor|level|storey|grundriss|pianta|pohja|plan d|niveau|kerros|etage|geschoss)\b/i],
  ['elevation', /\b(elevation|façade|facade|ansicht|prospetto|julkisivu)\b/i],
  ['section', /\b(section|schnitt|sezione|coupe|leikkaus)\b/i],
  ['detail', /\b(detail|dettaglio|détail|yksityiskohta)\b/i],
  ['schedule', /\b(schedule|legend|table|liste|tabella)\b/i],
  ['perspective', /\b(perspective|aerial|render|3d|view)\b/i],
  ['site', /\b(site|lageplan|asemapiirros)\b/i],
]
export const kindOf = (t?: string | null) => (t ? KIND.find(([, re]) => re.test(t))?.[0] ?? 'drawing' : 'drawing')

export function findViews(d: Drawing, sheet: SheetJSON | null, plan: Element[]): SheetView[] {
  // scans: the detector's floor boxes are the drawings
  const floors = plan.filter((p) => p.type === 'floor')
  if (floors.length >= 2) {
    return floors.map((f, i) => {
      const xs = f.polygon.map((p) => p[0]), ys = f.polygon.map((p) => p[1])
      return { id: `view${i + 1}`, title: `Floor ${i + 1}`, scale: null, kind: 'plan', box: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] as SheetView['box'] }
    }).sort((a, b) => a.box[0] - b.box[0]).map((v, i) => ({ ...v, id: `view${i + 1}`, title: `Floor ${i + 1}` }))
  }
  if (d.kind !== 'image') return [] // CAD and BIM sheets here are one drawing each
  let comps: number[][]
  try { comps = clusters(d) } catch { return [] }
  if (comps.length < 2) return []
  const texts = (sheet?.texts ?? []).filter((t) => t.bbox?.length === 4 && /[A-Za-zÀ-ÿ]{3,}/.test(t.text)).map((t) => ({ t: t.text.trim(), b: box(t.bbox, d), bold: !!t.bold }))
  const scales = (sheet?.scale?.candidates ?? []).filter((c) => c.bbox?.length === 4).map((c) => ({ t: c.text ?? `1:${c.denominator}`, b: box(c.bbox!, d) }))
  const H = d.height
  const views = comps.map((c, i): SheetView => {
    const [x0, y0, x1, y1] = c, ch = y1 - y0
    // the title sits under the drawing (or in its bottom strip), overlapping it horizontally
    let best: { t: string; b: number[]; s: number } | null = null
    for (const t of texts) {
      const [tx0, ty0, tx1, ty1] = t.b
      if (tx1 < x0 || tx0 > x1 || ty0 < y1 - 0.18 * ch || ty0 > y1 + 0.07 * H) continue
      const kw = kindOf(t.t) !== 'drawing' ? 3 : 0, size = (ty1 - ty0) / (0.01 * H), dist = Math.abs(ty0 - y1) / (0.02 * H)
      const s = kw + (t.bold ? 1 : 0) + Math.min(size, 3) + Math.min(t.t.length / 20, 1.5) - 0.5 * dist
      if (!best || s > best.s) best = { ...t, s }
    }
    let scale: string | null = null, sd = Infinity
    for (const sc of scales) {
      const [sx0, sy0, sx1] = sc.b
      if (sx1 < x0 || sx0 > x1 || sy0 < y1 - 0.25 * ch || sy0 > y1 + 0.09 * H) continue
      const dd = best ? Math.hypot(sc.b[0] - best.b[0], sc.b[1] - best.b[3]) : Math.abs(sy0 - y1)
      if (dd < sd) { sd = dd; scale = sc.t }
    }
    return { id: `view${i + 1}`, title: best?.t ?? null, scale, kind: kindOf(best?.t), box: c as SheetView['box'] }
  })
  return views
}

const box = (b: number[], d: Drawing) => { // sheet frame (rotated pages are turned by d.toPx)
  const p: Pt = d.toPx([b[0], b[1]]), q: Pt = d.toPx([b[2], b[3]])
  return [Math.min(p[0], q[0]), Math.min(p[1], q[1]), Math.max(p[0], q[0]), Math.max(p[1], q[1])]
}

// Ink clusters on a small copy of the page: long border and grid lines removed, nearby strokes merged; the title block
// (a tall strip at a side, or a wide strip along the bottom) is not a drawing.
function clusters(d: Drawing): number[][] {
  const src = d.source, iw = src instanceof HTMLImageElement ? src.naturalWidth : src.width, ih = src instanceof HTMLImageElement ? src.naturalHeight : src.height
  const k = 700 / Math.max(iw, ih), w = Math.max(1, Math.round(iw * k)), h = Math.max(1, Math.round(ih * k)), n = w * h
  const c = document.createElement('canvas')
  c.width = w; c.height = h
  const g = c.getContext('2d', { willReadFrequently: true })!
  g.fillStyle = '#fff'; g.fillRect(0, 0, w, h); g.drawImage(src, 0, 0, w, h)
  const px = g.getImageData(0, 0, w, h).data
  const m = new Uint8Array(n)
  for (let i = 0; i < n; i++) m[i] = 0.299 * px[4 * i] + 0.587 * px[4 * i + 1] + 0.114 * px[4 * i + 2] < 205 ? 1 : 0
  // long straight runs = sheet border, grid lines, title block rules
  const cut = new Uint8Array(n)
  for (let y = 0; y < h; y++) { let s = 0; for (let x = 0; x <= w; x++) { if (x < w && m[y * w + x]) continue; if (x - s > 0.22 * w) for (let q = s; q < x; q++) cut[y * w + q] = 1; s = x + 1 } }
  for (let x = 0; x < w; x++) { let s = 0; for (let y = 0; y <= h; y++) { if (y < h && m[y * w + x]) continue; if (y - s > 0.22 * h) for (let q = s; q < y; q++) cut[q * w + x] = 1; s = y + 1 } }
  for (let i = 0; i < n; i++) if (cut[i]) m[i] = 0
  // merge each drawing's strokes, dimensions and labels
  const r = Math.max(3, Math.round(0.011 * Math.max(w, h)))
  const grow = (a: Uint8Array, horiz: boolean) => {
    const o = new Uint8Array(n)
    if (horiz) for (let y = 0; y < h; y++) { let last = -1e9; for (let x = 0; x < w; x++) { if (a[y * w + x]) last = x; if (x - last <= r) o[y * w + x] = 1 } let nx = 1e9; for (let x = w - 1; x >= 0; x--) { if (a[y * w + x]) nx = x; if (nx - x <= r) o[y * w + x] = 1 } }
    else for (let x = 0; x < w; x++) { let last = -1e9; for (let y = 0; y < h; y++) { if (a[y * w + x]) last = y; if (y - last <= r) o[y * w + x] = 1 } let ny = 1e9; for (let y = h - 1; y >= 0; y--) { if (a[y * w + x]) ny = y; if (ny - y <= r) o[y * w + x] = 1 } }
    return o
  }
  const dil = grow(grow(m, true), false)
  // connected clusters
  const lab = new Int32Array(n).fill(-1), boxes: number[][] = [], stack: number[] = []
  for (let i = 0; i < n; i++) {
    if (!dil[i] || lab[i] >= 0) continue
    const id = boxes.length, b = [w, h, 0, 0, 0]
    lab[i] = id; stack.push(i)
    while (stack.length) {
      const j = stack.pop()!, x = j % w, y = (j / w) | 0
      if (x < b[0]) b[0] = x; if (y < b[1]) b[1] = y; if (x > b[2]) b[2] = x; if (y > b[3]) b[3] = y
      if (m[j]) b[4]++
      for (const q of [x > 0 ? j - 1 : -1, x < w - 1 ? j + 1 : -1, y > 0 ? j - w : -1, y < h - 1 ? j + w : -1])
        if (q >= 0 && dil[q] && lab[q] < 0) { lab[q] = id; stack.push(q) }
    }
    boxes.push(b)
  }
  const area = w * h
  const keep = boxes.filter(([x0, y0, x1, y1, ink]) => {
    const bw = x1 - x0 + 1, bh = y1 - y0 + 1
    if (bw * bh < 0.025 * area || ink < 0.002 * area) return false
    if (bw < 0.2 * w && bh > 0.45 * h && (x0 > 0.72 * w || x1 < 0.28 * w)) return false // title block column
    if (bh < 0.16 * h && bw > 0.35 * w && y0 > 0.78 * h) return false // title strip along the bottom
    if (x1 > 0.93 * w && y1 > 0.88 * h && x0 > 0.45 * w) return false // title block in the bottom-right corner
    return true
  })
  const s = d.width / w // small copy -> sheet px
  return keep.map(([x0, y0, x1, y1]) => [x0 * s, y0 * s, (x1 + 1) * s, (y1 + 1) * s]).sort((a, b) => a[0] - b[0] || a[1] - b[1])
}

// Which drawing an element lies in (its centre), or null
export function viewOf(views: SheetView[], poly: Pt[]) {
  if (!views.length || !poly.length) return null
  let cx = 0, cy = 0
  for (const [x, y] of poly) { cx += x; cy += y }
  cx /= poly.length; cy /= poly.length
  return views.find((v) => cx >= v.box[0] && cx <= v.box[2] && cy >= v.box[1] && cy <= v.box[3]) ?? null
}
