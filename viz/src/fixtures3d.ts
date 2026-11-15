// 3D models of the plan's symbols, built in code (no downloaded assets): WC, washbasin / sink, bathtub, shower, kitchen
// unit, sauna bench and stove, door leaves. Designed on the Claude Design canvas "Fixture models (A + C)": real
// proportions, ceramic white, chrome, oak, warm plaster. Each is sized from its detected box on the sheet and turned to the nearest
// wall; door leaves hinge on the side where the drawing shows the swing. Display only: heights are illustrative
// (2.6 m storey) and nothing here feeds an answer.
//
// Space: the 3D group's shape space (x, y = sheet px with y up, z = height in sheet px). The group's z scale is the lift.
import * as THREE from 'three'
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import type { Drawing, Loaded, Pt } from './types'
import { bbox, obb, toShape } from './geom'
import { normCls, type Cls } from './ink'

export type Mat = 'ceramic' | 'chrome' | 'wood' | 'front' | 'counter' | 'dark' | 'glass' | 'leaf' | 'stone' | 'basin' | 'plaster'
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
    basin: new THREE.MeshStandardMaterial({ color: '#dfe5e8', roughness: 0.25, envMapIntensity: 1.1 }), // inside of basins and tubs
    plaster: new THREE.MeshStandardMaterial({ color: '#efe8dd', roughness: 0.9, envMapIntensity: 0.85 }), // like the walls
  }
}

// Everything the 3D view furnishes: context fixtures (sheet / detector / BIM) and door openings.
export function buildFixtures(l: Loaded, wallH: number): Map<Mat, THREE.BufferGeometry> {
  const parts: Parts = new Map()
  const near = wallProbe(l.context.walls), benches = benchBoxes(l)
  for (const f of l.context.fixtures) {
    const c = normCls(f.cls)
    if (c) model(l.drawing, c, f.polygon, wallH, near, parts, f.cls, benches)
  }
  for (const o of l.context.openings) if (o.cls === 'door') model(l.drawing, 'door', o.polygon, wallH, near, parts)
  for (const e of l.guids.values()) if (e.leaf) leaf3d(l.drawing, e.leaf, wallH, parts) // BIM doors: the drawn leaf
  for (const r of l.context.railings ?? []) railing(l.drawing, r, wallH, parts)
  return merge(parts)
}

// One object's model as a single geometry (for the evidence highlight, which colours it in the answer colour).
export function buildOne(l: Loaded, cls: Cls, poly: Pt[], wallH: number, leaf?: Pt[] | null): THREE.BufferGeometry | null {
  const parts: Parts = new Map()
  if (leaf) leaf3d(l.drawing, leaf, wallH, parts)
  else model(l.drawing, cls, poly, wallH, wallProbe(l.context.walls), parts, rawOf(l, poly), benchBoxes(l))
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

// BIM railing: its own outline extruded to 1 m. Small outlines are balusters (chrome posts); longer ones glass with a
// chrome cap, following the drawn shape exactly (no guessed box)
function railing(d: Drawing, ring: Pt[], wallH: number, parts: Parts) {
  if (ring.length < 3) return
  const o = obb(ring), H = (m: number) => (m / 2.6) * wallH, pxm = wallH / 2.6
  if (o.len < 0.5) return
  const shape = new THREE.Shape(ring.map((p) => toShape(d, p)))
  const add = (m: Mat, g: THREE.BufferGeometry) => { const a = parts.get(m) ?? []; a.push(g); parts.set(m, a) }
  if (o.len < 0.12 * pxm) { add('chrome', new THREE.ExtrudeGeometry(shape, { depth: H(1.0), bevelEnabled: false })); return }
  add('glass', new THREE.ExtrudeGeometry(shape, { depth: H(0.96), bevelEnabled: false }))
  add('chrome', new THREE.ExtrudeGeometry(shape, { depth: H(0.04), bevelEnabled: false }).translate(0, 0, H(0.96)))
}

// BIM door leaf exactly as the model draws it (open), standing to door height
function leaf3d(d: Drawing, ring: Pt[], wallH: number, parts: Parts) {
  const s = new THREE.Shape(ring.map((p) => toShape(d, p)))
  const g = new THREE.ExtrudeGeometry(s, { depth: (2.05 / 2.6) * wallH, bevelEnabled: false })
  const a = parts.get('leaf') ?? []; a.push(g); parts.set('leaf', a)
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
function roundRect(w: number, h: number, r: number) {
  const s = new THREE.Shape(), x = -w / 2, y = -h / 2, rr = Math.min(r, w / 2, h / 2)
  s.moveTo(x + rr, y); s.lineTo(x + w - rr, y); s.absarc(x + w - rr, y + rr, rr, -Math.PI / 2, 0, false)
  s.lineTo(x + w, y + h - rr); s.absarc(x + w - rr, y + h - rr, rr, 0, Math.PI / 2, false)
  s.lineTo(x + rr, y + h); s.absarc(x + rr, y + h - rr, rr, Math.PI / 2, Math.PI, false)
  s.lineTo(x, y + rr); s.absarc(x + rr, y + rr, rr, Math.PI, 1.5 * Math.PI, false)
  return s
}

// a closed elliptic frustum along z: radii ru x rv at the top, `bot` times that at the bottom (pedestals, rims, lids)
const ellBody = (ru: number, rv: number, h: number, bot: number, x: number, y: number, z0: number, seg = 40) =>
  new THREE.CylinderGeometry(1, bot, h, seg).rotateX(Math.PI / 2).scale(ru, rv, 1).translate(x, y, z0 + h / 2)
const oval = (x: number, y: number, rx: number, ry: number) => { const p = new THREE.Path(); p.absellipse(x, y, rx, ry, 0, 2 * Math.PI, false, 0); return p }
// a square bar from a to b (handrails, shower arms)
function bar(a: THREE.Vector3, b: THREE.Vector3, t: number) {
  const dir = new THREE.Vector3().subVectors(b, a), len = dir.length()
  const g = new THREE.BoxGeometry(Math.max(len, 0.01), t, t)
  g.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(1, 0, 0), dir.normalize()))
  return g.translate((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2)
}

// ---- the models (see the design boards: each at real proportions inside its detected box, back to the nearest wall)
// sauna benches: a detector often finds a stepped bench tier by tier, as boxes side by side
const benchBoxes = (l: Loaded) => l.context.fixtures.filter((f) => /bench/i.test(f.cls)).map((f) => f.polygon)
const same = (a: Pt[], b: Pt[]) => { const p = bbox(a), q = bbox(b); return Math.abs(p.cx - q.cx) < 1 && Math.abs(p.cy - q.cy) < 1 && Math.abs(p.w - q.w) < 1 }
// the raw class of a highlighted fixture (benches, BIM furniture are told apart by it), so the highlight is the same model
const rawOf = (l: Loaded, poly: Pt[]) => l.context.fixtures.find((f) => same(f.polygon, poly))?.cls

function model(d: Drawing, cls: Cls, poly: Pt[], wallH: number, near: (p: Pt) => number, parts: Parts, raw?: string, benches: Pt[][] = []) {
  if (poly.length < 3) return
  const f = frameOf(d, poly, wallH), { L, D, H } = f
  if (L < 2 || D < 1) return
  const add = (m: Mat, g: THREE.BufferGeometry) => { const a = parts.get(m) ?? []; a.push(f.put(g)); parts.set(m, a) }
  // which end / side of the box is against a wall
  const endU = near(f.at(L / 2, 0)) <= near(f.at(-L / 2, 0)) ? 1 : -1
  const sideV = near(f.at(0, D / 2)) <= near(f.at(0, -D / 2)) ? 1 : -1
  const m = Math.min(L, D)
  const pxm = wallH / 2.6, Lm = L / pxm, Dm = D / pxm // metres (illustrative scale: one storey = 2.6 m)

  // BIM furniture: its own outline, extruded (not a box)
  if (raw && /Furnish|Furniture/.test(raw)) {
    const g = new THREE.ExtrudeGeometry(new THREE.Shape(poly.map((p) => toShape(d, p))), { depth: H(0.75), bevelEnabled: false })
    const a = parts.get('wood') ?? []; a.push(g); parts.set('wood', a)
    return
  }
  if (cls === 'stairs') {
    // a flight up the long side toward the wall end; near-square boxes: half a flight, then a landing at mid height
    const up = endU, run = 0.28 * pxm, square = Lm / Math.max(Dm, 1e-6) <= 1.3
    const span = square ? L / 2 : L, n = Math.max(3, Math.round(span / run)), top = square ? H(1.3) : H(2.6)
    for (let i = 0; i < n; i++) {
      const u0 = -L / 2 + (i / n) * span, u1 = -L / 2 + ((i + 1) / n) * span, h = ((i + 1) / n) * top
      const cu = -up * ((u0 + u1) / 2) // climbing toward `up`
      add('plaster', rbox(u1 - u0, D * 0.96, h, 0.01 * m, cu, 0, 0))
      add('wood', rbox((u1 - u0) * 1.02, D * 0.96, H(0.03), 0.004 * m, cu, 0, h - H(0.015)))
    }
    if (square) { // landing
      add('plaster', rbox(L / 2, D * 0.96, top - H(0.03), 0.01 * m, -up * (L / 4), 0, 0))
      add('wood', rbox(L / 2, D * 0.96, H(0.03), 0.004 * m, -up * (L / 4), 0, top - H(0.03)))
    }
    // balusters every third step and a sloped handrail on the open side (away from the nearest wall)
    const ov = -sideV * 0.45 * D, t = Math.max(0.5, 0.025 * pxm), uOf = (i: number) => -up * (-L / 2 + ((i + 0.5) / n) * span)
    for (let i = 0; i < n; i += 3) add('chrome', cyl(t / 2, H(0.9), uOf(i), ov, ((i + 1) / n) * top, 8))
    add('chrome', cyl(t / 2, H(0.9), uOf(n - 1), ov, top, 8))
    add('chrome', bar(new THREE.Vector3(uOf(0), ov, top / n + H(0.9)), new THREE.Vector3(uOf(n - 1), ov, top + H(0.9)), 1.6 * t))
    if (square) {
      add('chrome', bar(new THREE.Vector3(uOf(n - 1), ov, top + H(0.9)), new THREE.Vector3(-up * L * 0.48, ov, top + H(0.9)), 1.6 * t))
      add('chrome', cyl(t / 2, H(0.9), -up * L * 0.48, ov, top, 8))
    }
    return
  }
  if (cls === 'shaft') { // installation shaft: a plastered full-height duct, access panel on the room side, grille on top
    add('plaster', rbox(0.94 * L, 0.94 * D, H(2.6), 0.02 * m, 0, 0, 0))
    add('front', rbox(0.4 * L, Math.max(0.5, 0.02 * D), H(0.6), 0.1, 0, -sideV * 0.475 * D, H(0.4)))
    for (let i = 1; i < 6; i++) add('chrome', rbox(0.9 * L, Math.max(0.4, 0.02 * D), H(0.01), 0.1, 0, (i / 6 - 0.5) * 0.9 * D, H(2.6)))
    return
  }
  if (cls === 'washer') {
    add('front', rbox(0.94 * L, 0.94 * D, H(0.85), 0.06 * m, 0, 0, 0))
    // porthole on the face away from the nearest wall
    const alongV = near(f.at(0, sideV * D / 2)) <= near(f.at(endU * L / 2, 0)), r = 0.28 * m
    const ring = new THREE.CylinderGeometry(r, r, Math.max(0.5, 0.04 * m), 28), glass = new THREE.CylinderGeometry(r * 0.78, r * 0.78, Math.max(0.6, 0.05 * m), 28)
    const place = (g: THREE.BufferGeometry) => (alongV ? g.translate(0, -sideV * 0.47 * D, H(0.45)) : g.rotateZ(Math.PI / 2).translate(-endU * 0.47 * L, 0, H(0.45)))
    add('chrome', place(ring)); add('dark', place(glass))
    const fw = alongV ? L : D
    add('leaf', place(rbox(0.27 * fw, Math.max(0.6, 0.04 * m), H(0.06), 0.1, -0.2 * fw, 0, H(0.31)))) // detergent drawer
    add('ceramic', rbox(0.94 * L, 0.94 * D, H(0.012), 0.01 * m, 0, 0, H(0.85)))
    return
  }
  if (cls === 'other' || cls === 'cabinet') { // the CAD fixture layer's unlabelled pieces, read by size
    const long = Math.max(Lm, Dm), short = Math.min(Lm, Dm)
    if (long <= 0.36) { add('front', rbox(L, D, H(2.6), 0.03 * m, 0, 0, 0)); return } // post / column casing
    if (long >= 1 && long / short >= 2) { // wardrobe: tall, wood fronts with door seams on the room side
      add('dark', rbox(0.96 * L, 0.9 * D, H(0.06), 0.01 * m, 0, 0, 0)) // plinth
      add('wood', rbox(0.98 * L, 0.96 * D, H(1.94), 0.02 * m, 0, 0, H(0.06)))
      const k = Math.max(2, Math.round(Lm / 0.5)), fr = -sideV * 0.48 * D
      for (let i = 1; i < k; i++) add('dark', rbox(Math.max(0.4, 0.008 * L), 0.02 * D, H(1.9), 0.1, -L / 2 + (i / k) * L, fr, H(0.08)))
      for (let i = 1; i < k; i += 2) for (const g of [-1, 1]) // bar handles either side of every other seam
        add('chrome', rbox(Math.max(0.5, 0.02 * pxm), 0.04 * D, H(0.4), 0.2, -L / 2 + (i / k) * L + g * 0.05 * pxm, fr - sideV * 0.02 * D, H(0.9)))
      return
    }
    add('front', rbox(0.96 * L, 0.96 * D, H(0.86), 0.04 * m, 0, 0, 0)) // appliance / cabinet unit with a counter top
    add('counter', rbox(L, D, H(0.03), 0.02 * m, 0, 0, H(0.86)))
    add('chrome', rbox(0.8 * L, 0.04 * D, H(0.02), 0.2, 0, -sideV * 0.5 * D, H(0.78))) // bar handle
    return
  }

  if (cls === 'wc') {
    // close-coupled WC, 0.38 x 0.65 m at real proportions inside the box: cistern (to 0.80 m) against the wall, a tapered
    // pedestal into an oval rim, closed seat and lid (0.43 m), chrome flush button. Depth runs along the box's long side,
    // unless the box is near-square with the wall on a long side.
    const byV = L / D < 1.3 && near(f.at(0, sideV * D / 2)) < near(f.at(endU * L / 2, 0))
    const Ld = byV ? D : L, Wd = byV ? L : D, s = byV ? 1 : endU
    const q = Math.min(Ld / 0.68, Wd / 0.4) // plan px per metre of the toilet itself
    const y = (t: number) => s * (Ld / 2 - t * q) // metres from the wall -> along the depth axis
    const wc: [Mat, THREE.BufferGeometry][] = [
      ['ceramic', rbox(0.17 * q, 0.38 * q, H(0.4), 0.035 * q, y(0.085), 0, H(0.4))], // cistern
      ['chrome', cyl(0.03 * q, H(0.012), y(0.085), 0, H(0.8))], // flush button
      ['ceramic', ellBody(0.24 * q, 0.16 * q, H(0.36), 0.58, y(0.3), 0, 0)], // pedestal into the bowl
      ['ceramic', ellBody(0.29 * q, 0.18 * q, H(0.04), 1, y(0.36), 0, H(0.36))], // rim
      ['front', ellBody(0.26 * q, 0.165 * q, H(0.03), 1.04, y(0.37), 0, H(0.4))], // closed seat and lid
    ]
    for (const [mt, g] of wc) add(mt, byV ? g.rotateZ((sideV * Math.PI) / 2) : g)
  } else if (cls === 'sink') {
    // washbasin on a wall-hung vanity (two doors, bar handles), ceramic top with an oval basin sunk into it, chrome mixer
    // at the wall; a long box is a kitchen sink run (steel basin)
    const s = sideV, big = L > 1.6 * D, fr = -s * 0.47 * D // the room-side face
    const z0 = big ? 0.1 : 0.14 // a vanity hangs off the floor; a kitchen run stands on a plinth
    if (big) add('dark', rbox(0.95 * L, 0.88 * D, H(0.1), 0.01 * m, 0, s * 0.03 * D, 0))
    add('front', rbox(0.97 * L, 0.94 * D, H(0.75 - z0), 0.02 * m, 0, 0, H(z0)))
    add('dark', rbox(Math.max(0.4, 0.006 * L), 0.02 * D, H(0.55), 0.1, 0, fr, H(0.17))) // door seam
    for (const k of [-1, 1]) add('chrome', rbox(Math.max(0.5, 0.012 * L), 0.04 * D, H(0.1), 0.2, k * 0.065 * L, fr - s * 0.02 * D, H(0.62)))
    const ru = big ? 0.22 * L : 0.34 * L, rv = 0.31 * D, bv = -s * 0.06 * D
    const top = roundRect(L, D, 0.04 * m)
    top.holes.push(oval(0, bv, ru, rv))
    add('ceramic', new THREE.ExtrudeGeometry(top, { depth: H(0.08), bevelEnabled: false, curveSegments: 14 }).translate(0, 0, H(0.75)))
    add(big ? 'chrome' : 'basin', ellBody(ru, rv, H(0.005), 1, 0, bv, H(0.75))) // basin floor
    add('dark', cyl(0.03 * m, H(0.004), 0, bv, H(0.755))) // drain
    add('chrome', cyl(0.025 * m, H(0.17), 0, s * 0.375 * D, H(0.83))) // mixer
    add('chrome', rbox(0.04 * m, 0.27 * D, 0.035 * m, 0.015 * m, 0, s * 0.24 * D, H(0.97))) // spout
  } else if (cls === 'bathtub') {
    // built-in tub with an apron, oval basin, chrome filler and spout at the wall end
    const out = roundRect(L, D, 0.05 * m)
    out.holes.push(oval(0, 0, 0.44 * L, 0.38 * D))
    add('ceramic', new THREE.ExtrudeGeometry(out, { depth: H(0.56), bevelEnabled: false, curveSegments: 16 }))
    add('basin', ellBody(0.44 * L, 0.38 * D, H(0.15), 1, 0, 0, 0)) // the tub's floor
    add('chrome', cyl(0.025 * m, H(0.12), endU * 0.47 * L, 0, H(0.56), 12))
    add('chrome', rbox(0.08 * L, 0.035 * m, 0.035 * m, 0.012 * m, endU * 0.43 * L, 0, H(0.655)))
  } else if (cls === 'shower') {
    add('ceramic', rbox(L, D, H(0.05), 0.05 * m, 0, 0, 0))
    add('dark', cyl(0.07 * m, H(0.004), 0, 0, H(0.05)))
    const edges: { u: number; v: number; len: number; along: 'u' | 'v'; dist: number }[] = [
      { u: L / 2, v: 0, len: D, along: 'v' as const, dist: near(f.at(L / 2, 0)) }, { u: -L / 2, v: 0, len: D, along: 'v' as const, dist: near(f.at(-L / 2, 0)) },
      { u: 0, v: D / 2, len: L, along: 'u' as const, dist: near(f.at(0, D / 2)) }, { u: 0, v: -D / 2, len: L, along: 'u' as const, dist: near(f.at(0, -D / 2)) },
    ].sort((a, b) => b.dist - a.dist)
    const t = Math.max(0.6, 0.02 * m)
    for (const e of edges.slice(0, 2)) if (e.dist > 0.2 * m) { // glass on the open sides, chrome cap on top
      add('glass', e.along === 'u' ? rbox(e.len, t, H(1.95), t / 3, e.u, e.v, H(0.05)) : rbox(t, e.len, H(1.95), t / 3, e.u, e.v, H(0.05)))
      add('chrome', e.along === 'u' ? rbox(e.len, 2 * t, H(0.025), t / 2, e.u, e.v, H(2.0)) : rbox(2 * t, e.len, H(0.025), t / 2, e.u, e.v, H(2.0)))
    }
    const w = edges.slice(2) // the two wall sides: pole in their corner
    const cu = w.find((e) => e.along === 'v')?.u ?? L / 2, cv = w.find((e) => e.along === 'u')?.v ?? D / 2
    const pu = cu - Math.sign(cu) * 0.08 * m, pv = cv - Math.sign(cv) * 0.08 * m
    const hu = pu - Math.sign(cu) * 0.12 * m, hv = pv - Math.sign(cv) * 0.12 * m
    add('chrome', cyl(0.018 * m, H(2.05), pu, pv, 0)) // riser rail
    add('chrome', rbox(0.09 * m, 0.09 * m, H(0.1), 0.02 * m, pu, pv, H(1.0))) // mixer
    add('chrome', cyl(0.12 * m, H(0.02), hu, hv, H(2.0), 28)) // rain head
    add('chrome', bar(new THREE.Vector3(pu, pv, H(2.04)), new THREE.Vector3(hu, hv, H(2.04)), 0.025 * m)) // its arm
  } else if (cls === 'kitchen') {
    const s = sideV
    add('dark', rbox(0.98 * L, 0.9 * D, H(0.1), 0.01 * m, 0, s * 0.02 * D, 0))
    add('front', rbox(L, 0.96 * D, H(0.76), 0.02 * m, 0, s * 0.02 * D, H(0.1)))
    add('counter', rbox(L, D, H(0.04), 0.02 * m, 0, 0, H(0.86)))
    const fr = -s * 0.46 * D, k = Math.max(1, Math.round(Lm / 0.6)) // fronts about 0.6 m wide: seams and bar handles
    for (let i = 1; i < k; i++) add('dark', rbox(Math.max(0.4, 0.004 * L), 0.02 * D, H(0.72), 0.1, -L / 2 + (i / k) * L, fr, H(0.12)))
    for (let i = 0; i < k; i++) add('chrome', rbox(Math.min(0.24 * pxm, (0.6 * L) / k), 0.04 * D, H(0.02), 0.2, -L / 2 + ((i + 0.5) / k) * L, fr - s * 0.02 * D, H(0.78)))
    if (L > 1.5 * D) {
      add('dark', rbox(0.3 * D * 1.6, 0.6 * D, H(0.006), 0.02 * m, -0.25 * L, -s * 0.05 * D, H(0.9))) // hob
      add('chrome', rbox(0.5 * D, 0.5 * D, H(0.004), 0.05 * m, 0.22 * L, -s * 0.05 * D, H(0.9))) // sink
      add('chrome', cyl(0.025 * D, H(0.25), 0.22 * L, s * 0.36 * D, H(0.9), 12)) // mixer
      add('chrome', rbox(0.035 * D, 0.25 * D, 0.035 * D, 0.012 * D, 0.22 * L, s * 0.25 * D, H(1.12)))
    }
  } else if (cls === 'stove') {
    add('dark', rbox(0.84 * L, 0.84 * D, H(0.62), 0.04 * m, 0, 0, 0))
    add('chrome', rbox(0.84 * L, 0.84 * D, H(0.02), 0.01 * m, 0, 0, H(0.62))) // steel top
    const n = 4, r = 0.085 * m
    for (let a = 0; a < n; a++) for (let b = 0; b < n; b++)
      add('stone', new THREE.SphereGeometry(r, 10, 8).scale(1, 1, 0.75).translate(((a + 0.5) / n - 0.5) * 0.68 * L, ((b + 0.5) / n - 0.5) * 0.68 * D, H(0.64) + r * 0.5))
    // open wooden guard on the two room sides (away from the nearest wall end and side)
    const gu = -endU * 0.47 * L, gv = -sideV * 0.47 * D, pw = Math.max(0.6, 0.035 * pxm)
    for (const [pu, pv] of [[gu, gv], [-gu, gv], [gu, -gv]]) add('wood', rbox(pw, pw, H(0.8), 0.1, pu, pv, 0))
    for (const z of [0.35, 0.72]) {
      add('wood', rbox(0.94 * L, pw * 0.8, H(0.04), 0.1, 0, gv, H(z)))
      add('wood', rbox(pw * 0.8, 0.94 * D, H(0.04), 0.1, gu, 0, H(z)))
    }
  } else if (cls === 'door') {
    const sw = swing(d, f), hinge = sw.hinge, side = sw.side
    const t = Math.max(1, 0.22 * D), w = 0.96 * L, a = (32 * Math.PI) / 180 * -hinge * side
    const leaf = rbox(w, t, H(2.03), t * 0.3, -hinge * w / 2, 0, 0).rotateZ(a).translate(hinge * L / 2, side * D / 2, 0)
    add('leaf', leaf)
    const hx = -hinge * (w - 0.08 * L), hz = H(1.0)
    const knob = rbox(0.1 * L, t * 2.6, Math.max(0.6, 0.03 * L), t * 0.3, hx, 0, hz).rotateZ(a).translate(hinge * L / 2, side * D / 2, 0)
    add('chrome', knob)
    const at = Math.max(0.6, 0.05 * pxm), ad = Math.max(0.4, 0.02 * pxm), av = side * (D / 2 + ad / 2) // architrave
    for (const k of [-1, 1]) add('front', rbox(at, ad, H(2.15), 0.1, k * (L / 2 + at / 2), av, 0))
    add('front', rbox(L + 2 * at, ad, at, 0.1, 0, av, H(2.1)))
  } else if (cls === 'fixture' && /bench/.test(raw ?? '')) {
    // two tiers when deep enough: the upper (0.9 m) against the wall, the lower (0.45 m) on the room side; slats on supports
    // a box next to another bench box is one tier of a stepped bench: upper (0.88 m) if it backs onto a wall, else lower
    const b = bbox(poly), gap = 0.15 * pxm
    const stacked = benches.some((o) => !same(o, poly) && (() => { const c = bbox(o); return c.x0 < b.x1 + gap && b.x0 < c.x1 + gap && c.y0 < b.y1 + gap && b.y0 < c.y1 + gap })())
    const tiers: [number, number][] = stacked ? [[0, near(f.at(0, (sideV * D) / 2)) < 0.2 * pxm ? 0.88 : 0.43]]
      : Dm > 1.1 ? [[(sideV * D) / 4, 0.88], [(-sideV * D) / 4, 0.43]] : [[0, 0.43]]
    const dep = tiers.length > 1 ? D / 2 : D, n = 5, legs = L > 1.4 * pxm ? [-0.45, 0, 0.45] : [-0.45, 0.45]
    for (const [cv, z] of tiers) {
      for (let i = 0; i < n; i++) add('wood', rbox(L, (dep / n) * 0.8, H(0.035), 0.01 * m, 0, cv + ((i + 0.5) / n - 0.5) * dep, H(z)))
      for (const su of legs) add('wood', rbox(Math.max(0.6, 0.06 * pxm), 0.84 * dep, H(z), 0.005 * m, su * L, cv, 0))
    }
  } else if (cls === 'column') {
    add('front', rbox(L, D, H(2.6), 0.03 * m, 0, 0, 0))
  } else if (cls === 'fixture') { // an unnamed sanitary or fixture symbol: a white ceramic unit
    add('ceramic', rbox(0.96 * L, 0.96 * D, H(0.6), 0.12 * m, 0, 0, 0))
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
