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

export const ringArea = (p: Pt[]) => {
  let a = 0
  for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length]; a += x1 * y2 - x2 * y1 }
  return a / 2
}

// Closed rings (sheet px) -> extrudable shapes. Even-odd nesting: a ring inside a solid ring is its hole,
// a ring inside a hole is solid again. Wall layers mix free-standing wall bodies with a few that wrap a shaft.
export function shapesFromRings(d: Drawing, rings: Pt[][], disjoint = false): THREE.Shape[] {
  if (disjoint) return rings.filter((r) => r.length >= 3).map((r) => shapeOf(d, r)) // image rectangles never nest
  const rs = rings.filter((r) => r.length >= 3 && Math.abs(ringArea(r)) > 1e-3)
    .map((r) => ({ r, a: Math.abs(ringArea(r)), depth: 0, parent: -1 }))
    .sort((p, q) => q.a - p.a)
  // strict containment: walls that merely touch share corner points, and a shared point must not make a hole
  const boxes = rs.map((x) => bbox(x.r))
  const contains = (j: number, i: number) => {
    const o = boxes[j], c = boxes[i], m = 1e-6 * Math.max(o.w, o.h)
    return c.x0 > o.x0 + m && c.y0 > o.y0 + m && c.x1 < o.x1 - m && c.y1 < o.y1 - m && inside([c.cx, c.cy], rs[j].r)
  }
  rs.forEach((cur, i) => {
    for (let j = i - 1; j >= 0; j--) // smallest bigger ring that contains this one
      if (contains(j, i)) { cur.parent = j; cur.depth = rs[j].depth + 1; break }
  })
  const shapes = new Map<number, THREE.Shape>()
  rs.forEach((cur, i) => {
    if (cur.depth % 2 === 0) shapes.set(i, shapeOf(d, cur.r))
    else shapes.get(cur.parent)?.holes.push(new THREE.Path(cur.r.map((p) => toShape(d, p))))
  })
  return [...shapes.values()]
}

// Rough wall thickness of a thin polygon: 2 * area / perimeter (exact for long rectangles).
export const thickness = (r: Pt[]) => {
  let per = 0
  for (let i = 0; i < r.length; i++) { const [x1, y1] = r[i], [x2, y2] = r[(i + 1) % r.length]; per += Math.hypot(x2 - x1, y2 - y1) }
  return per > 0 ? (2 * Math.abs(ringArea(r))) / per : 0
}

// Oriented box of a roughly rectangular outline: axis along its longest edge (robust for rectangles, where PCA is not).
export function obb(pts: Pt[]) {
  let best = 0, ux = 1, uy = 0
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length], l = Math.hypot(x2 - x1, y2 - y1)
    if (l > best) { best = l; ux = (x2 - x1) / l; uy = (y2 - y1) / l }
  }
  const vx = -uy, vy = ux
  let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity
  for (const [x, y] of pts) { const u = x * ux + y * uy, v = x * vx + y * vy; u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v) }
  const cu = (u0 + u1) / 2, cv = (v0 + v1) / 2
  return { cx: cu * ux + cv * vx, cy: cu * uy + cv * vy, ux, uy, vx, vy, len: u1 - u0, dep: v1 - v0 }
}

// Corners of an oriented box, optionally with its depth scaled (glass pane) or shrunk on all sides (bevelled fixtures).
export function obbRect(o: ReturnType<typeof obb>, depScale = 1, shrink = 0): Pt[] {
  const hl = Math.max(o.len / 2 - shrink, 0.5), hd = Math.max((o.dep * depScale) / 2 - shrink, 0.5)
  return [[-hl, -hd], [hl, -hd], [hl, hd], [-hl, hd]].map(([a, b]) => [o.cx + a * o.ux + b * o.vx, o.cy + a * o.uy + b * o.vy] as Pt)
}

// A pipe along a polyline at its printed diameter (world space, centred on y = 0).
export function tube(d: Drawing, pts: Pt[], r: number) {
  const path = new THREE.CurvePath<THREE.Vector3>()
  const w = pts.map((p) => { const [x, z] = toWorld(d, p); return new THREE.Vector3(x, 0, z) })
  for (let i = 0; i + 1 < w.length; i++) path.add(new THREE.LineCurve3(w[i], w[i + 1]))
  return new THREE.TubeGeometry(path, Math.max(16, w.length * 6), Math.max(r, 0.5), 14, false)
}

// What a measured sheet pixel is worth, and where that scale comes from (shown with every measurement, like takeoff
// tools that insist on confirming the scale first).
export function measureScale(d: Drawing): { f: number; unit: string; areaUnit: string; source: string } {
  if (d.mPerPx) return { f: d.mPerPx, unit: 'm', areaUnit: 'm²', source: d.kind === 'vector' ? 'scale: CAD coordinates in metres' : 'scale: from the sheet' }
  if (d.units === 'svg') {
    const f = Math.abs(d.fromPx([1, 0])[0] - d.fromPx([0, 0])[0])
    return { f, unit: 'mm on paper', areaUnit: 'mm² on paper', source: 'drawing scale not given: paper millimetres' }
  }
  return { f: 1, unit: 'px', areaUnit: 'px²', source: 'no scale on this sheet: pixels' }
}
