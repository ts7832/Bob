// Display-only walls for scanned plans, found in the image itself (the backend has none for scans without its
// segmentation model). Dark pixels -> morphological opening, which removes text,
// door swings and furniture lines but keeps wall strokes -> runs merged into rectangles (plans are mostly
// axis-aligned). Never used to answer anything.
import type { Pt } from './types'

const WORK = 1200 // long side of the working copy, px

export function wallsFromImage(img: HTMLImageElement, width: number, height: number): Pt[][] {
  const s = Math.min(1, WORK / Math.max(img.naturalWidth, img.naturalHeight))
  const w = Math.max(1, Math.round(img.naturalWidth * s)), h = Math.max(1, Math.round(img.naturalHeight * s))
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

  // 2. opening size: strokes thinner than k vanish. Tuned on the CubiCasa samples (475-1240 px): k 3-4 keeps inner
  //    and outer walls and drops text, door swings and furniture; sizing it from the thickest walls lost the inner ones.
  const k = Math.max(3, Math.round(Math.max(w, h) / 300))

  // 3. opening = erosion then dilation with a k x k square (separable)
  mask = dilate(erode(mask, w, h, k), w, h, k)

  // 4. runs per row merged into rectangles
  const rects = rectangles(mask, w, h)
  const minArea = k * k * 3 // drop specks
  const sx = width / w, sy = height / h // working px -> sheet px (sheet frame = render width/height)
  return rects
    .filter((r) => (r.x1 - r.x0) * (r.y1 - r.y0) >= minArea)
    .map((r) => [[r.x0 * sx, r.y0 * sy], [r.x1 * sx, r.y0 * sy], [r.x1 * sx, r.y1 * sy], [r.x0 * sx, r.y1 * sy]] as Pt[])
}

// Min (erode) / max (dilate) over a k-wide window, rows then columns.
function filter(m: Uint8Array, w: number, h: number, k: number, isErode: boolean) {
  const a = Math.floor((k - 1) / 2), b = k - 1 - a
  const tmp = new Uint8Array(w * h), out = new Uint8Array(w * h)
  const pass = (src: Uint8Array, dst: Uint8Array, n: number, lines: number, idx: (line: number, i: number) => number) => {
    for (let l = 0; l < lines; l++) {
      // prefix sums of the line make each window an O(1) lookup
      const pre = new Int32Array(n + 1)
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
