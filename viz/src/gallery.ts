// QA page (dev only, not in the bundle): every fixture model on its own tile, built by the same code as the 3D view
// (buildFixtures / buildOne) from synthetic boxes at real sizes, each with its back to a wall like on a plan.
// Open http://localhost:5180/models.html. Compare with the Claude Design boards "Fixture models (A + C)".
import * as THREE from 'three'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import { buildFixtures, buildOne, fixtureMaterials } from './fixtures3d'
import { toShape } from './geom'
import { normCls } from './ink'
import type { Context, Drawing, Loaded, Pt } from './types'

const PXM = 40, WALL_H = 2.6 * PXM, CELL = 5 * PXM, COLS = 5
type Item = { label: string; cls: string; w: number; d: number; side?: boolean; door?: boolean; rail?: boolean; hl?: 'answer' | 'inspect' }
const items: Item[] = [
  { label: 'WC', cls: 'wc', w: 0.4, d: 0.68 },
  { label: 'WC (squarer box, wall on the long side)', cls: 'wc', w: 0.6, d: 0.5 },
  { label: 'Washbasin on vanity', cls: 'washbasin', w: 0.62, d: 0.48 },
  { label: 'Bathtub', cls: 'bathtub', w: 1.7, d: 0.75 },
  { label: 'Shower', cls: 'shower', w: 0.9, d: 0.9, side: true },
  { label: 'Kitchen unit', cls: 'kitchen_unit', w: 2.4, d: 0.6 },
  { label: 'Kitchen sink run', cls: 'sink', w: 1.2, d: 0.6 },
  { label: 'Washing machine', cls: 'washing_machine', w: 0.6, d: 0.6 },
  { label: 'Appliance / cabinet (other)', cls: 'other', w: 0.6, d: 0.6 },
  { label: 'Wardrobe (other, long)', cls: 'other', w: 2.0, d: 0.6 },
  { label: 'Post (other, small)', cls: 'other', w: 0.25, d: 0.25 },
  { label: 'Sauna bench', cls: 'sauna_bench', w: 2.0, d: 1.2 },
  { label: 'Sauna stove', cls: 'sauna_stove', w: 0.5, d: 0.5, side: true },
  { label: 'Stairs', cls: 'stairs', w: 1.1, d: 3.0, side: true },
  { label: 'Stairs (square box: landing)', cls: 'stairs', w: 2.2, d: 2.0 },
  { label: 'Shaft', cls: 'shaft', w: 1.0, d: 0.8 },
  { label: 'Door (plan opening)', cls: 'door', w: 0.9, d: 0.2, door: true },
  { label: 'Railing (BIM)', cls: 'railing', w: 1.6, d: 0.05, rail: true },
  { label: 'Counted WC (answer)', cls: 'wc', w: 0.4, d: 0.68, hl: 'answer' },
  { label: 'Inspected washbasin', cls: 'washbasin', w: 0.62, d: 0.48, hl: 'inspect' },
]
const rect = (x0: number, y0: number, x1: number, y1: number): Pt[] => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]

const rows = Math.ceil(items.length / COLS)
const W = COLS * CELL, Hh = rows * CELL
const src = document.createElement('canvas'); src.width = W; src.height = Hh
const g2 = src.getContext('2d')!; g2.fillStyle = '#fff'; g2.fillRect(0, 0, W, Hh)
const drawing: Drawing = { key: 'gallery', kind: 'image', width: W, height: Hh, source: src, toPx: (p) => p, fromPx: (p) => p, units: 'px', mPerPx: 1 / PXM }
const ctx: Context = { walls: [], rooms: [], openings: [], fixtures: [], railings: [] }
const centres: { x: number; y: number; size: number }[] = []
const hl: { cls: string; poly: Pt[]; kind: 'answer' | 'inspect' }[] = []
items.forEach((it, i) => {
  const cx0 = (i % COLS) * CELL, cy0 = Math.floor(i / COLS) * CELL
  const wy = cy0 + 1.2 * PXM, w = it.w * PXM, d = it.d * PXM, x0 = cx0 + CELL / 2 - w / 2 // back wall along the top
  if (it.door) {
    ctx.walls.push(rect(cx0 + 0.4 * PXM, wy, x0, wy + d), rect(x0 + w, wy, cx0 + CELL - 0.4 * PXM, wy + d))
    ctx.openings.push({ cls: 'door', polygon: rect(x0, wy, x0 + w, wy + d) })
  } else {
    ctx.walls.push(rect(cx0 + 0.4 * PXM, wy - 0.2 * PXM, cx0 + CELL - 0.4 * PXM, wy))
    if (it.side) ctx.walls.push(rect(x0 - 0.2 * PXM, wy - 0.2 * PXM, x0, wy + d + 0.4 * PXM)) // a side wall on the left
  }
  const poly = rect(x0, wy + (it.door ? 0 : 0), x0 + w, wy + d)
  if (it.rail) ctx.railings!.push(rect(x0, wy + 1.0 * PXM, x0 + w, wy + 1.0 * PXM + d))
  else if (it.hl) hl.push({ cls: it.cls, poly, kind: it.hl })
  else if (!it.door) ctx.fixtures.push({ cls: it.cls, polygon: poly })
  centres.push({ x: x0 + w / 2, y: wy + Math.max(d, it.rail ? 1.0 * PXM : 0) / 2 + (it.rail ? 0.5 * PXM : 0), size: Math.max(it.w, it.d, 1.1) })
})
const loaded: Loaded = { drawing, plan: [], context: ctx, guids: new Map(), classes: new Map(), views: [] }

// scene: the view's own space (shape x, y up, z height under a -90° X rotation), studio light like Model.tsx
const scene = new THREE.Scene(); scene.background = new THREE.Color('#f1ece5')
const group = new THREE.Group(); group.rotation.x = -Math.PI / 2; scene.add(group)
const mats = fixtureMaterials()
for (const [m, geo] of buildFixtures(loaded, WALL_H)) { const mesh = new THREE.Mesh(geo, mats[m]); mesh.castShadow = mesh.receiveShadow = true; group.add(mesh) }
for (const h of hl) {
  const geo = buildOne(loaded, normCls(h.cls)!, h.poly, WALL_H)
  const c = h.kind === 'answer' ? '#ff7a00' : '#2f6fde'
  if (geo) { const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 0.35, roughness: 0.4 })); mesh.castShadow = true; group.add(mesh) }
}
const plaster = new THREE.MeshStandardMaterial({ color: '#efe8dd', roughness: 0.9 })
for (const w of ctx.walls) {
  const geo = new THREE.ExtrudeGeometry(new THREE.Shape(w.map((p) => toShape(drawing, p))), { depth: 1.3 * PXM, bevelEnabled: false })
  const mesh = new THREE.Mesh(geo, plaster); mesh.castShadow = mesh.receiveShadow = true; group.add(mesh)
}
const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, Hh), new THREE.MeshStandardMaterial({ color: '#e4e0d8', roughness: 0.9 }))
floor.receiveShadow = true; group.add(floor)

const T = 300, canvas = document.createElement('canvas')
document.body.style.cssText = 'margin:0;background:#f1ece5;font:13px system-ui,sans-serif;color:#2a2622'
const wrap = document.createElement('div'); wrap.style.cssText = `position:relative;width:${COLS * T}px`
wrap.appendChild(canvas); document.body.appendChild(wrap)
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true })
renderer.setPixelRatio(Math.min(2, devicePixelRatio)); renderer.setSize(COLS * T, rows * T)
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap
const pmrem = new THREE.PMREMGenerator(renderer)
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture; scene.environmentIntensity = 0.62
scene.add(new THREE.HemisphereLight('#fff8ee', '#8f857a', 0.55))
const sun = new THREE.DirectionalLight('#ffe9cc', 2.3); sun.position.set(-0.62 * W, 1.1 * W, -0.4 * W); sun.castShadow = true
Object.assign(sun.shadow.camera, { left: -W, right: W, top: W, bottom: -W, near: 1, far: 4 * W }); sun.shadow.camera.updateProjectionMatrix()
sun.shadow.mapSize.set(4096, 4096); sun.shadow.bias = -0.0004; scene.add(sun)

const cam = new THREE.PerspectiveCamera(32, 1, 1, 10000)
renderer.setScissorTest(true)
items.forEach((it, i) => {
  const c = centres[i], col = i % COLS, row = Math.floor(i / COLS)
  const [wx, wz] = [c.x - W / 2, c.y - Hh / 2], dist = c.size * PXM * 2.6
  cam.position.set(wx + 0.55 * dist, 0.75 * dist, wz + 0.9 * dist); cam.lookAt(wx, 0.45 * PXM, wz); cam.updateProjectionMatrix()
  const y = (rows - 1 - row) * T
  renderer.setViewport(col * T, y, T, T); renderer.setScissor(col * T, y, T, T); renderer.render(scene, cam)
  const tag = document.createElement('div'); tag.textContent = it.label
  tag.style.cssText = `position:absolute;left:${col * T + 8}px;top:${row * T + 6}px;background:#fffc;padding:2px 6px;border-radius:6px`
  wrap.appendChild(tag)
})
document.title = 'Fixture models'
;(window as unknown as { galleryReady: boolean }).galleryReady = true
