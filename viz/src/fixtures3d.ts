// 3D models of the plan's symbols, built in code (no downloaded assets): WC, washbasin / sink, bathtub, shower, kitchen
// unit, sauna bench and stove, door leaves. Each is sized from its detected box on the sheet and turned to the nearest
// wall; door leaves hinge on the side where the drawing shows the swing. Display only: heights are illustrative
// (2.6 m storey) and nothing here feeds an answer.
//
// Space: the 3D group's shape space (x, y = sheet px with y up, z = height in sheet px). The group's z scale is the lift.
import * as THREE from 'three'
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import type { Drawing, Loaded, Pt } from './types'
import { obb, toShape } from './geom'
import { normCls, type Cls } from './ink'

export type Mat = 'ceramic' | 'chrome' | 'wood' | 'front' | 'counter' | 'dark' | 'glass' | 'leaf' | 'stone'
type Parts = Map<Mat, THREE.BufferGeometry[]>

export function fixtureMaterials(): Record<Mat, THREE.Material> {
  return {
    ceramic: new THREE.MeshPhysicalMaterial({ color: '#fbfaf7', roughness: 0.16, clearcoat: 0.7, clearcoatRoughness: 0.1, envMapIntensity: 1.15 }),
    chrome: new THREE.MeshStandardMaterial({ color: '#dfe3e8', metalness: 1, roughness: 0.16, envMapIntensity: 1.4 }),
    wood: new THREE.MeshStandardMaterial({ color: '#b98b5c', roughness: 0.55, envMapIntensity: 0.9 }),
    front: new THREE.MeshStandardMaterial({ color: '#f3f2ee', roughness: 0.45, envMapIntensity: 1 }),
    counter: new THREE.MeshStandardMaterial({ color: '#474c55', roughness: 0.3, envMapIntensity: 1.1 }),
    dark: new THREE.MeshStandardMaterial({ color: '#2b2e34', roughness: 0.55, metalness: 0.25 }),
    glass: new THREE.MeshStandardMaterial({ color: '#cfe4ee', roughness: 0.04, metalness: 0.1, transparent: true, opacity: 0.3, envMapIntensity: 1.6, depthWrite: false }),
    leaf: new THREE.MeshStandardMaterial({ color: '#ece5d8', roughness: 0.5, envMapIntensity: 0.9 }),
    stone: new THREE.MeshStandardMaterial({ color: '#80848b', roughness: 0.95 }),
  }
}

// Everything the 3D view furnishes: context fixtures (sheet / detector / BIM) and door openings.
export function buildFixtures(l: Loaded, wallH: number): Map<Mat, THREE.BufferGeometry> {
  const parts: Parts = new Map()
  const near = wallProbe(l.context.walls)
  for (const f of l.context.fixtures) {
    const c = normCls(f.cls)
    if (c) model(l.drawing, c, f.polygon, wallH, near, parts, f.cls)
  }
  for (const o of l.context.openings) if (o.cls === 'door') model(l.drawing, 'door', o.polygon, wallH, near, parts)
  return merge(parts)
}

// One object's model as a single geometry (for the evidence highlight, which colours it in the answer colour).
export function buildOne(l: Loaded, cls: Cls, poly: Pt[], wallH: number): THREE.BufferGeometry | null {
  const parts: Parts = new Map()
  model(l.drawing, cls, poly, wallH, wallProbe(l.context.walls), parts)
  const all = [...parts.entries()].filter(([m]) => m !== 'glass').flatMap(([, g]) => g)
  if (!all.length) return null
  const g = mergeGeometries(all.map((x) => (x.index ? x.toNonIndexed() : x)).map(strip))
  all.forEach((x) => x.dispose())
  return g
}

function merge(parts: Parts) {
  const out = new Map<Mat, THREE.BufferGeometry>()
  for (const [m, gs] of parts) {
    if (!gs.length) continue
    out.set(m, mergeGeometries(gs.map((x) => (x.index ? x.toNonIndexed() : x)).map(strip)))
    gs.forEach((x) => x.dispose())
  }
  return out
}
const strip = (g: THREE.BufferGeometry) => { // same attribute set everywhere so they merge
  for (const k of Object.keys(g.attributes)) if (k !== 'position' && k !== 'normal' && k !== 'uv') g.deleteAttribute(k)
  if (!g.attributes.uv) g.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array((g.attributes.position.count) * 2), 2))
  return g
}

// ---- local frame of a detected box: u along its long side, v across, z up (all sheet px)
type Frame = { L: number; D: number; H: (m: number) => number; put: (g: THREE.BufferGeometry) => THREE.BufferGeometry; at: (u: number, v: number) => Pt }
function frameOf(d: Drawing, poly: Pt[], wallH: number): Frame {
  const o = obb(poly), th = Math.atan2(-o.uy, o.ux), c = toShape(d, [o.cx, o.cy])
  const m = new THREE.Matrix4().makeRotationZ(th).setPosition(c.x, c.y, 0)
  return {
    L: o.len, D: o.dep, H: (mm) => (mm / 2.6) * wallH,
    put: (g) => g.applyMatrix4(m),
    at: (u, v) => [o.cx + u * o.ux + v * o.uy, o.cy + u * o.uy - v * o.ux], // local -> sheet px (for wall checks)
  }
}

// distance from a sheet point to the nearest wall (px); walls far away are skipped by their boxes
function wallProbe(walls: Pt[][]) {
  const boxes = walls.map((w) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
    for (const [x, y] of w) { if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y }
    return [x0, y0, x1, y1]
  })
  return (p: Pt) => {
    let best = Infinity
    for (let i = 0; i < walls.length; i++) {
      const b = boxes[i], dx = Math.max(b[0] - p[0], 0, p[0] - b[2]), dy = Math.max(b[1] - p[1], 0, p[1] - b[3])
      if (Math.hypot(dx, dy) >= best) continue
      const w = walls[i]
      for (let j = 0; j < w.length; j++) best = Math.min(best, segDist(p, w[j], w[(j + 1) % w.length]))
    }
    return best
  }
}
function segDist(p: Pt, a: Pt, b: Pt) {
  const vx = b[0] - a[0], vy = b[1] - a[1], l2 = vx * vx + vy * vy
  const t = l2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / l2)) : 0
  return Math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy)
}

// ---- primitives (local frame)
const rbox = (w: number, dd: number, h: number, r: number, x: number, y: number, z0: number) => {
  const rr = Math.max(0.001, Math.min(r, w / 2, dd / 2, h / 2) * 0.98)
  return new RoundedBoxGeometry(Math.max(w, 0.01), Math.max(dd, 0.01), Math.max(h, 0.01), 3, rr).translate(x, y, z0 + h / 2)
}
const cyl = (r: number, h: number, x: number, y: number, z0: number, seg = 20) =>
  new THREE.CylinderGeometry(r, r, h, seg).rotateX(Math.PI / 2).translate(x, y, z0 + h / 2)
// a turned profile (r 0..1, z in m) stretched to an ellipse ru x rv: bowls, basins
const lathe = (prof: [number, number][], ru: number, rv: number, x: number, y: number, H: (m: number) => number) =>
  new THREE.LatheGeometry(prof.map(([r, z]) => new THREE.Vector2(r, H(z))), 40).rotateX(Math.PI / 2).scale(ru, rv, 1).translate(x, y, 0)
function roundRect(w: number, h: number, r: number) {
  const s = new THREE.Shape(), x = -w / 2, y = -h / 2, rr = Math.min(r, w / 2, h / 2)
  s.moveTo(x + rr, y); s.lineTo(x + w - rr, y); s.absarc(x + w - rr, y + rr, rr, -Math.PI / 2, 0, false)
  s.lineTo(x + w, y + h - rr); s.absarc(x + w - rr, y + h - rr, rr, 0, Math.PI / 2, false)
  s.lineTo(x + rr, y + h); s.absarc(x + rr, y + h - rr, rr, Math.PI / 2, Math.PI, false)
  s.lineTo(x, y + rr); s.absarc(x + rr, y + rr, rr, Math.PI, 1.5 * Math.PI, false)
  return s
}

// ---- the models
function model(d: Drawing, cls: Cls, poly: Pt[], wallH: number, near: (p: Pt) => number, parts: Parts, raw?: string) {
  if (poly.length < 3) return
  const f = frameOf(d, poly, wallH), { L, D, H } = f
  if (L < 2 || D < 1) return
  const add = (m: Mat, g: THREE.BufferGeometry) => { const a = parts.get(m) ?? []; a.push(f.put(g)); parts.set(m, a) }
  // which end / side of the box is against a wall
  const endU = near(f.at(L / 2, 0)) <= near(f.at(-L / 2, 0)) ? 1 : -1
  const sideV = near(f.at(0, D / 2)) <= near(f.at(0, -D / 2)) ? 1 : -1
  const m = Math.min(L, D)

  if (cls === 'wc') {
    const s = endU, td = 0.26 * L
    add('ceramic', rbox(td, 0.86 * D, H(0.42), 0.12 * D, s * (L / 2 - td / 2), 0, H(0.38))) // cistern
    add('chrome', cyl(0.07 * D, H(0.012), s * (L / 2 - td / 2), 0, H(0.8)))
    add('ceramic', lathe([[0.42, 0], [0.4, 0.08], [0.52, 0.2], [0.8, 0.3], [0.97, 0.37], [1, 0.4], [0.93, 0.42], [0.78, 0.41], [0.6, 0.33], [0.35, 0.22], [0, 0.2]],
      0.49 * (L - td), 0.46 * D, -s * td / 2, 0, H)) // pedestal and bowl
  } else if (cls === 'sink') {
    const s = sideV, big = L > 1.6 * D // a long box is a kitchen sink run
    add('front', rbox(0.97 * L, 0.94 * D, H(0.8), 0.04 * m, 0, 0, 0))
    add('ceramic', rbox(L, D, H(0.05), 0.08 * m, 0, 0, H(0.8)))
    const ru = big ? 0.2 * L : 0.36 * L, rv = 0.34 * D
    add(big ? 'chrome' : 'ceramic', lathe([[0, 0.83], [0.8, 0.835], [0.97, 0.88], [1, 0.92], [0.92, 0.925], [0.75, 0.885], [0, 0.865]], ru, rv, 0, -s * 0.05 * D, H))
    add('chrome', cyl(0.035 * m, H(0.16), 0, s * 0.38 * D, H(0.85)))
    add('chrome', rbox(0.05 * m, 0.22 * D, 0.05 * m, 0.02 * m, 0, s * 0.3 * D, H(0.98)))
  } else if (cls === 'bathtub') {
    const i = 0.09 * m, out = roundRect(L, D, 0.14 * m), inner = roundRect(L - 2 * i, D - 2 * i, 0.3 * m)
    out.holes.push(inner)
    add('ceramic', new THREE.ExtrudeGeometry(out, { depth: H(0.55), bevelEnabled: false, curveSegments: 10 }))
    add('ceramic', new THREE.ExtrudeGeometry(inner, { depth: H(0.1), bevelEnabled: false, curveSegments: 10 }))
    add('chrome', cyl(0.03 * m, H(0.12), endU * (L / 2 - i / 2), 0, H(0.55)))
  } else if (cls === 'shower') {
    add('ceramic', rbox(L, D, H(0.05), 0.05 * m, 0, 0, 0))
    add('dark', cyl(0.07 * m, H(0.004), 0, 0, H(0.05)))
    const edges: { u: number; v: number; len: number; along: 'u' | 'v'; dist: number }[] = [
      { u: L / 2, v: 0, len: D, along: 'v' as const, dist: near(f.at(L / 2, 0)) }, { u: -L / 2, v: 0, len: D, along: 'v' as const, dist: near(f.at(-L / 2, 0)) },
      { u: 0, v: D / 2, len: L, along: 'u' as const, dist: near(f.at(0, D / 2)) }, { u: 0, v: -D / 2, len: L, along: 'u' as const, dist: near(f.at(0, -D / 2)) },
    ].sort((a, b) => b.dist - a.dist)
    const t = Math.max(0.6, 0.02 * m)
    for (const e of edges.slice(0, 2)) if (e.dist > 0.2 * m) // glass on the open sides
      add('glass', e.along === 'u' ? rbox(e.len, t, H(1.95), t / 3, e.u, e.v, H(0.05)) : rbox(t, e.len, H(1.95), t / 3, e.u, e.v, H(0.05)))
    const w = edges.slice(2) // the two wall sides: pole in their corner
    const cu = w.find((e) => e.along === 'v')?.u ?? L / 2, cv = w.find((e) => e.along === 'u')?.v ?? D / 2
    const pu = cu - Math.sign(cu) * 0.08 * m, pv = cv - Math.sign(cv) * 0.08 * m
    add('chrome', cyl(0.018 * m, H(2.05), pu, pv, 0))
    add('chrome', cyl(0.12 * m, H(0.02), pu - Math.sign(cu) * 0.12 * m, pv - Math.sign(cv) * 0.12 * m, H(2.0)))
  } else if (cls === 'kitchen') {
    const s = sideV
    add('dark', rbox(0.98 * L, 0.9 * D, H(0.1), 0.01 * m, 0, s * 0.02 * D, 0))
    add('front', rbox(L, 0.96 * D, H(0.76), 0.02 * m, 0, s * 0.02 * D, H(0.1)))
    add('counter', rbox(L, D, H(0.04), 0.02 * m, 0, 0, H(0.86)))
    if (L > 1.5 * D) {
      add('dark', rbox(0.3 * D * 1.6, 0.6 * D, H(0.006), 0.02 * m, -0.25 * L, -s * 0.05 * D, H(0.9))) // hob
      add('chrome', rbox(0.5 * D, 0.5 * D, H(0.004), 0.05 * m, 0.22 * L, -s * 0.05 * D, H(0.9))) // sink
    }
  } else if (cls === 'stove') {
    add('dark', rbox(0.92 * L, 0.92 * D, H(0.7), 0.06 * m, 0, 0, 0))
    const n = 3, r = 0.11 * m
    for (let a = 0; a < n; a++) for (let b = 0; b < n; b++)
      add('stone', new THREE.SphereGeometry(r, 10, 8).translate(((a + 0.5) / n - 0.5) * 0.7 * L, ((b + 0.5) / n - 0.5) * 0.7 * D, H(0.7) + r * 0.6))
  } else if (cls === 'door') {
    const sw = swing(d, f), hinge = sw.hinge, side = sw.side
    const t = Math.max(1, 0.22 * D), w = 0.96 * L, a = (32 * Math.PI) / 180 * -hinge * side
    const leaf = rbox(w, t, H(2.03), t * 0.3, -hinge * w / 2, 0, 0).rotateZ(a).translate(hinge * L / 2, side * D / 2, 0)
    add('leaf', leaf)
    const hx = -hinge * (w - 0.08 * L), hz = H(1.0)
    const knob = rbox(0.1 * L, t * 2.6, Math.max(0.6, 0.03 * L), t * 0.3, hx, 0, hz).rotateZ(a).translate(hinge * L / 2, side * D / 2, 0)
    add('chrome', knob)
  } else if (cls === 'fixture' && /bench/.test(raw ?? '')) {
    const n = 5
    for (let i = 0; i < n; i++) add('wood', rbox(L, (D / n) * 0.78, H(0.035), 0.01 * m, 0, ((i + 0.5) / n - 0.5) * D, H(0.42)))
    for (const su of [-1, 1]) for (const sv of [-1, 1]) add('wood', rbox(0.05 * L, 0.08 * D, H(0.42), 0.005 * m, su * 0.42 * L, sv * 0.38 * D, 0))
  } else if (cls !== 'window' && cls !== 'stairs' && cls !== 'column') {
    const soft = /Furnish|Furniture/.test(raw ?? '')
    add(soft ? 'wood' : 'ceramic', rbox(0.96 * L, 0.96 * D, H(soft ? 0.72 : 0.6), 0.12 * m, 0, 0, 0))
  }
}

// Door swing from the drawing itself: the side of the opening with more linework is where the leaf and arc are drawn,
// and the hinge is the end where the leaf line runs away from the wall.
function swing(d: Drawing, f: Frame): { side: 1 | -1; hinge: 1 | -1 } {
  const lum = lumOf(d)
  if (!lum) return { side: 1, hinge: 1 }
  const ink = (u0: number, u1: number, v0: number, v1: number) => {
    let n = 0
    for (let i = 0; i < 36; i++) for (let j = 0; j < 36; j++) {
      const [x, y] = f.at(u0 + ((i + 0.5) / 36) * (u1 - u0), v0 + ((j + 0.5) / 36) * (v1 - v0))
      if (lum.dark(x, y)) n++
    }
    return n
  }
  const { L, D } = f
  const plus = ink(-L / 2, L / 2, D / 2 + 0.05 * L, D / 2 + L), minus = ink(-L / 2, L / 2, -D / 2 - L, -D / 2 - 0.05 * L)
  const side: 1 | -1 = plus >= minus ? 1 : -1
  const v0 = side > 0 ? D / 2 : -D / 2 - L, v1 = side > 0 ? D / 2 + L : -D / 2
  const hingeP = ink(L / 2 - 0.12 * L, L / 2 + 0.04 * L, v0, v1), hingeM = ink(-L / 2 - 0.04 * L, -L / 2 + 0.12 * L, v0, v1)
  return { side, hinge: hingeP >= hingeM ? 1 : -1 }
}

const lumCache = new WeakMap<object, { dark: (x: number, y: number) => boolean } | null>()
function lumOf(d: Drawing) {
  if (lumCache.has(d.source)) return lumCache.get(d.source)!
  let res: { dark: (x: number, y: number) => boolean } | null = null
  try {
    const src = d.source, sw = src instanceof HTMLImageElement ? src.naturalWidth : src.width, sh = src instanceof HTMLImageElement ? src.naturalHeight : src.height
    const k = Math.min(1, 2400 / Math.max(sw, sh)), w = Math.max(1, Math.round(sw * k)), h = Math.max(1, Math.round(sh * k))
    const c = document.createElement('canvas')
    c.width = w; c.height = h
    const g = c.getContext('2d', { willReadFrequently: true })!
    g.fillStyle = '#fff'; g.fillRect(0, 0, w, h); g.drawImage(src, 0, 0, w, h)
    const px = g.getImageData(0, 0, w, h).data, sx = w / d.width, sy = h / d.height
    res = {
      dark: (x, y) => {
        const X = Math.floor(x * sx), Y = Math.floor(y * sy)
        if (X < 0 || Y < 0 || X >= w || Y >= h) return false
        const i = 4 * (Y * w + X)
        return 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2] < 150
      },
    }
  } catch { res = null }
  lumCache.set(d.source, res)
  return res
}
