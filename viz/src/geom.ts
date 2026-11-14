import * as THREE from 'three'
import type { Drawing, Element, Pt } from './types'

// The only place image pixels become world coordinates.
// Drawing lies on the XZ ground plane, centred at the origin, 1 world unit = 1 image pixel.
// Image +y (down) → world +z, so a camera looking down with up = -z shows the image upright.
export const toWorld = (d: Drawing, [x, y]: Pt): [number, number] => [x - d.width / 2, y - d.height / 2]

// Shapes are built in XY and the mesh is rotated -90° about X: shape (x, y) → world (x, 0, -y).
export const toShape = (d: Drawing, p: Pt): THREE.Vector2 => {
  const [wx, wz] = toWorld(d, p)
  return new THREE.Vector2(wx, -wz)
}

export const bbox = (pts: Pt[]) => {
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
  const x0 = Math.min(...xs), y0 = Math.min(...ys), x1 = Math.max(...xs), y1 = Math.max(...ys)
  return { x0, y0, x1, y1, w: x1 - x0, h: y1 - y0, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 }
}

// Label anchor: polygon centroid, falling back to bbox centre if it lands outside (L-shapes).
export const anchor = (el: Element): Pt => {
  const p = el.polygon
  if (p.length < 3) return p[0]
  let a = 0, cx = 0, cy = 0
  for (let i = 0; i < p.length; i++) {
    const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length]
    const f = x1 * y2 - x2 * y1
    a += f; cx += (x1 + x2) * f; cy += (y1 + y2) * f
  }
  if (Math.abs(a) < 1e-6) { const b = bbox(p); return [b.cx, b.cy] }
  const c: Pt = [cx / (3 * a), cy / (3 * a)]
  return inside(c, p) ? c : [bbox(p).cx, bbox(p).cy]
}

export const inside = ([x, y]: Pt, poly: Pt[]) => {
  let c = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c
  }
  return c
}

// Points elements get a small square so they can be hit, outlined and cropped like the rest.
export const outline = (el: Element, r = 18): Pt[] => {
  if (el.geometry !== 'point') return el.polygon
  const [x, y] = el.polygon[0]
  return [[x - r, y - r], [x + r, y - r], [x + r, y + r], [x - r, y + r]]
}

export const shapeOf = (d: Drawing, pts: Pt[]) => {
  const s = new THREE.Shape(pts.map((p) => toShape(d, p)))
  return s
}
