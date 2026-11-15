// Display-only walls for scanned plans, found in the image itself (the backend has none for scans without its
// segmentation model). Dark pixels -> morphological opening, which removes text,
// door swings and furniture lines but keeps wall strokes -> runs merged into rectangles (plans are mostly
// axis-aligned). Never used to answer anything.
import type { Pt } from './types'

const WORK = 2400 // long side of the working copy, px (full sheets need it: inner walls must stay >= 3 px)

export function wallsFromImage(img: HTMLImageElement | HTMLCanvasElement, width: number, height: number): Pt[][] {
  return scanLayout(img, width, height).walls
}

// Walls plus the rooms between them: free space bounded by the walls (door gaps closed for the cut, then each room
// grown back up to the real walls). Each room is a list of rectangles in sheet px. Display only (3D floors, clean view).
export function scanLayout(img: HTMLImageElement | HTMLCanvasElement, width: number, height: number, gaps: Pt[][] = []): { walls: Pt[][]; rooms: Pt[][][] } {
  const iw = img instanceof HTMLImageElement ? img.naturalWidth : img.width, ih = img instanceof HTMLImageElement ? img.naturalHeight : img.height
  const s = Math.min(1, WORK / Math.max(iw, ih))
  const w = Math.max(1, Math.round(iw * s)), h = Math.max(1, Math.round(ih * s))
  const c = document.createElement('canvas')
  c.width = w; c.height = h
  const g = c.getContext('2d', { willReadFrequently: true })!
  g.fillStyle = '#fff'; g.fillRect(0, 0, w, h)
  g.drawImage(img, 0, 0, w, h)
  const px = g.getImageData(0, 0, w, h).data

  // 1. dark mask (luminance; wall poché in these plans is near black)
  let mask = new Uint8Array(w * h)
  for (let i = 0; i < w * h; i++) {
    const y = 0.299 * px[4 * i] + 0.587 * px[4 * i + 1] + 0.114 * px[4 * i + 2]
    mask[i] = y < 90 ? 1 : 0
  }

  // 2. opening size from the sheet's own thinnest strokes: k = hairline width + 2, so hairlines, text, door swings and
  //    furniture vanish and every wall at least k wide stays. Gives k 3-4 on the CubiCasa scans (the values tuned by
  //    hand there) and k 3 on full A1/A2 sheets at the working size. Sizing it from the thickest walls lost inner walls.
  const k = Math.min(9, Math.max(3, hairline(mask, w, h) + 2))

  // 3. opening = erosion then dilation with a k x k square (separable)
  mask = dilate(erode(mask, w, h, k), w, h, k)

  // 4. keep only large connected wall networks: letters, dimension text, logos and symbols are small separate pieces
  //    (bold headings on full sheets survived the opening and stood up as wall 'pillars')
  mask = keepLarge(mask, w, h, 0.04 * Math.max(w, h))

  // 5. runs per row merged into rectangles
  const rects = rectangles(mask, w, h)
  const minArea = k * k * 3 // drop specks
  const sx = width / w, sy = height / h // working px -> sheet px (sheet frame = render width/height)
  const kept = rects.filter((r) => (r.x1 - r.x0) * (r.y1 - r.y0) >= minArea)
  // a handful of thick pieces is not a building (title-block borders, one thick-outlined foundation): no walls then
  if (kept.length < 12) return { walls: [], rooms: [] }
  const box = (r: Rect): Pt[] => [[r.x0 * sx, r.y0 * sy], [r.x1 * sx, r.y0 * sy], [r.x1 * sx, r.y1 * sy], [r.x0 * sx, r.y1 * sy]]
  // door and window boxes the detector found close their gaps, so rooms split exactly at the doors
  const gapRects = gaps.map((g) => { const xs = g.map((p) => p[0] / sx), ys = g.map((p) => p[1] / sy); return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) } })
  return { walls: kept.map(box), rooms: roomsOf(mask, w, h, gapRects).map((rs) => rs.map(box)) }
}

// Rooms = free-space regions inside the walls. Door gaps are closed by thickening the walls by g, regions touching the
// border (outside) and specks are dropped, then each region grows back (through free space only) to the real walls.
function roomsOf(wall: Uint8Array, w: number, h: number, gaps: Rect[] = []): Rect[][] {
  const n = w * h, g = Math.max(3, Math.round((gaps.length ? 0.012 : 0.022) * Math.max(w, h))) // detected doors: thin seal is enough
  const thick = dilate(wall, w, h, 2 * g + 1)
  for (const r of gaps) // seal each detected door / window opening
    for (let y = Math.max(0, Math.floor(r.y0) - 1); y < Math.min(h, Math.ceil(r.y1) + 1); y++)
      for (let x = Math.max(0, Math.floor(r.x0) - 1); x < Math.min(w, Math.ceil(r.x1) + 1); x++) thick[y * w + x] = 1
  const lab = new Int32Array(n).fill(-1), q = new Int32Array(n), sizes: number[] = []
  let id = 0
  for (let s = 0; s < n; s++) {
    if (thick[s] || lab[s] >= 0) continue
    let head = 0, tail = 0, border = false
    q[tail++] = s; lab[s] = id
    while (head < tail) {
      const i = q[head++], x = i % w, y = (i / w) | 0
      if (x === 0 || y === 0 || x === w - 1 || y === h - 1) border = true
      for (const j of [x > 0 ? i - 1 : -1, x < w - 1 ? i + 1 : -1, y > 0 ? i - w : -1, y < h - 1 ? i + w : -1])
        if (j >= 0 && !thick[j] && lab[j] < 0) { lab[j] = id; q[tail++] = j }
    }
    sizes.push(border ? -1 : tail) // -1: outside
    id++
  }
  const minArea = 0.002 * n
  const keep = sizes.map((sz) => sz >= minArea)
  for (let i = 0; i < n; i++) if (lab[i] >= 0 && !keep[lab[i]]) lab[i] = -1
  // grow back to the walls: breadth-first through free (not wall) pixels, at most g + 1 steps
  let front: number[] = []
  for (let i = 0; i < n; i++) if (lab[i] >= 0) front.push(i)
  for (let step = 0; step <= g && front.length; step++) {
    const next: number[] = []
    for (const i of front) {
      const x = i % w, y = (i / w) | 0
      for (const j of [x > 0 ? i - 1 : -1, x < w - 1 ? i + 1 : -1, y > 0 ? i - w : -1, y < h - 1 ? i + w : -1])
        if (j >= 0 && !wall[j] && lab[j] < 0) { lab[j] = lab[i]; next.push(j) }
    }
    front = next
  }
  const out: Rect[][] = []
  for (let r = 0; r < sizes.length; r++) {
    if (!keep[r]) continue
    const m = new Uint8Array(n)
    let any = false
    for (let i = 0; i < n; i++) if (lab[i] === r) { m[i] = 1; any = true }
    if (any) out.push(rectangles(m, w, h))
  }
  return out
}

// Most common dark run length (1-6 px) across rows and columns, counted per run: the width of hairlines and text.
function hairline(m: Uint8Array, w: number, h: number) {
  const n = new Array(8).fill(0)
  const add = (r: number) => { if (r >= 1 && r <= 6) n[r]++ }
  for (let y = 0; y < h; y += 2) { let r = 0; for (let x = 0; x <= w; x++) { if (x < w && m[y * w + x]) r++; else { add(r); r = 0 } } }
  for (let x = 0; x < w; x += 2) { let r = 0; for (let y = 0; y <= h; y++) { if (y < h && m[y * w + x]) r++; else { add(r); r = 0 } } }
  let best = 1
  for (let r = 1; r <= 6; r++) if (n[r] > n[best]) best = r
  return best
}

// Connected components (4-neighbour) of the mask; components whose bounding-box diagonal is below minDiag are dropped.
function keepLarge(m: Uint8Array, w: number, h: number, minDiag: number) {
  const seen = new Uint8Array(w * h), out = new Uint8Array(w * h), q = new Int32Array(w * h)
  for (let s = 0; s < w * h; s++) {
    if (!m[s] || seen[s]) continue
    let head = 0, tail = 0, x0 = w, y0 = h, x1 = 0, y1 = 0
    q[tail++] = s; seen[s] = 1
    while (head < tail) {
      const i = q[head++], x = i % w, y = (i / w) | 0
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y
      if (x > 0 && m[i - 1] && !seen[i - 1]) { seen[i - 1] = 1; q[tail++] = i - 1 }
      if (x < w - 1 && m[i + 1] && !seen[i + 1]) { seen[i + 1] = 1; q[tail++] = i + 1 }
      if (y > 0 && m[i - w] && !seen[i - w]) { seen[i - w] = 1; q[tail++] = i - w }
      if (y < h - 1 && m[i + w] && !seen[i + w]) { seen[i + w] = 1; q[tail++] = i + w }
    }
    if (Math.hypot(x1 - x0, y1 - y0) >= minDiag) for (let k = 0; k < tail; k++) out[q[k]] = 1
  }
  return out
}

// Min (erode) / max (dilate) over a k-wide window, rows then columns.
function filter(m: Uint8Array, w: number, h: number, k: number, isErode: boolean) {
  const a = Math.floor((k - 1) / 2), b = k - 1 - a
  const tmp = new Uint8Array(w * h), out = new Uint8Array(w * h)
  const pass = (src: Uint8Array, dst: Uint8Array, n: number, lines: number, idx: (line: number, i: number) => number) => {
    const pre = new Int32Array(n + 1) // prefix sums of the line make each window an O(1) lookup; one buffer per pass
    for (let l = 0; l < lines; l++) {
      for (let i = 0; i < n; i++) pre[i + 1] = pre[i] + src[idx(l, i)]
      for (let i = 0; i < n; i++) {
        const lo = Math.max(0, i - a), hi = Math.min(n, i + b + 1), sum = pre[hi] - pre[lo]
        dst[idx(l, i)] = isErode ? (sum === hi - lo && hi - lo === k ? 1 : 0) : (sum > 0 ? 1 : 0)
      }
    }
  }
  pass(m, tmp, w, h, (y, x) => y * w + x)
  pass(tmp, out, h, w, (x, y) => y * w + x)
  return out
}
const erode = (m: Uint8Array, w: number, h: number, k: number) => filter(m, w, h, k, true)
const dilate = (m: Uint8Array, w: number, h: number, k: number) => filter(m, w, h, k, false)

type Rect = { x0: number; y0: number; x1: number; y1: number }

// Horizontal runs per row; a run identical to one in the row above extends that rectangle downwards.
function rectangles(m: Uint8Array, w: number, h: number): Rect[] {
  const done: Rect[] = []
  let open = new Map<string, Rect>()
  for (let y = 0; y < h; y++) {
    const next = new Map<string, Rect>()
    let x = 0
    while (x < w) {
      if (!m[y * w + x]) { x++; continue }
      const x0 = x
      while (x < w && m[y * w + x]) x++
      const key = `${x0}:${x}`
      const r = open.get(key)
      if (r) { r.y1 = y + 1; next.set(key, r); open.delete(key) } else next.set(key, { x0, y0: y, x1: x, y1: y + 1 })
    }
    for (const r of open.values()) done.push(r)
    open = next
  }
  for (const r of open.values()) done.push(r)
  return done
}
