import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import * as THREE from 'three'
import { useFrame, useThree, type ThreeEvent } from '@react-three/fiber'
import { CameraControls, Html, Line, PerformanceMonitor } from '@react-three/drei'
import CameraControlsImpl from 'camera-controls'
import { Building, Effects, Floors, Lighting, drawingUV, useBuilding, useLifted, type Lift } from './Model'
import { Photo } from './Photo'
import type { Answer, Drawing, Element, Loaded, Pt, Tier } from './types'
import { GLYPH, SYMBOL, inkOf, type Cls } from './ink'
import { anchor, bbox, measureScale, outline, ringArea, shapeOf, thickness, toShape, toWorld, tube } from './geom'

export const ACCENT = '#ff7a00' // = the demo page's --accent
const LOW = '#e0a100' // low-confidence detections
const LOW_CONF = 0.6
const CLICK_PX = 3 // pointer travel above this is a drag (pan / orbit), not a click
const ACTION = CameraControlsImpl.ACTION
const TILT = { azimuth: -0.45, polar: 0.8 } // 3D view: about 53° from straight down, turned a little

// Colours per element type for the faint "found on this sheet" layer (same palette as demo/index.html).
const KIND_COLOR: Record<string, string> = {
  door: '#2563eb', window: '#0d9488', wc: '#9333ea', sink: '#db2777', bathtub: '#0891b2', shower: '#4f46e5',
  sauna_stove: '#b45309', sauna_bench: '#a16207', bedroom: '#3b82f6', kitchen: '#eab308', bathroom: '#10b981',
  wc_room: '#a855f7', balcony: '#84cc16', sauna_room: '#ef4444', living_room: '#f97316', dining: '#d97706', room: '#6366f1',
  entry: '#0ea5e9', storage: '#78716c', utility: '#14b8a6', dressing_room: '#ec4899', terrace: '#65a30d', garage: '#475569',
  other_room: '#94a3b8', wall: '#374151', apartment: '#d64545', floor: '#111827',
}

const MAX_TEX = 4096 // full-size sheets render up to ~6600 px; the GPU gets a downscaled copy, crops still read the original

function useDrawingTexture(d: Drawing) {
  const tex = useMemo(() => {
    let src: HTMLImageElement | HTMLCanvasElement = d.source
    const w = src instanceof HTMLImageElement ? src.naturalWidth : src.width
    const h = src instanceof HTMLImageElement ? src.naturalHeight : src.height
    if (Math.max(w, h) > MAX_TEX) {
      const k = MAX_TEX / Math.max(w, h), c = document.createElement('canvas')
      c.width = Math.round(w * k); c.height = Math.round(h * k)
      const g = c.getContext('2d')!
      g.imageSmoothingQuality = 'high'
      g.drawImage(src, 0, 0, c.width, c.height)
      src = c
    }
    const t = src instanceof HTMLCanvasElement ? new THREE.CanvasTexture(src) : new THREE.Texture(src)
    t.colorSpace = THREE.SRGBColorSpace
    t.anisotropy = 8
    t.needsUpdate = true
    return t
  }, [d.key])
  useEffect(() => () => tex.dispose(), [tex])
  return tex
}

// Diagonal stripes for "estimated" answers; repeats in sheet pixels so the hatch is the same everywhere.
function useHatch(d: Drawing) {
  const tex = useMemo(() => {
    const c = document.createElement('canvas')
    c.width = c.height = 32
    const g = c.getContext('2d')!
    g.strokeStyle = '#fff'; g.lineWidth = 7
    for (const o of [-32, 0, 32]) { g.beginPath(); g.moveTo(o, 32); g.lineTo(o + 32, 0); g.stroke() }
    const t = new THREE.CanvasTexture(c)
    t.wrapS = t.wrapT = THREE.RepeatWrapping
    const cell = Math.max(d.width, d.height) / 220
    t.repeat.set(d.width / cell, d.height / cell)
    return t
  }, [d.key])
  useEffect(() => () => tex.dispose(), [tex])
  return tex
}

// Illustrative wall height in sheet px: 2.6 m when the sheet has a scale (BIM: assumed 1:100), else 10x the median wall thickness.
function wallHeight(l: Loaded) {
  const d = l.drawing, max = Math.max(d.width, d.height)
  if (d.mPerPx) return 2.6 / d.mPerPx
  if (d.units === 'svg') { // BIM plan SVGs are in paper millimetres: assume a 1:100 sheet, 2.6 m = 26 mm
    const pxPerUnit = 1 / Math.abs(d.fromPx([1, 0])[0] - d.fromPx([0, 0])[0])
    return THREE.MathUtils.clamp(26 * pxPerUnit, 0.02 * max, 0.14 * max)
  }
  const t = l.context.walls.map(thickness).filter((v) => v > 0).sort((a, b) => a - b)
  if (t.length) return THREE.MathUtils.clamp(10 * t[t.length >> 1], 0.02 * max, 0.14 * max)
  return 0.035 * max
}

export type Insets = { l: number; t: number; r: number; b: number } // px of the view covered by cards / toolbar

export type Focus = { x0: number; y0: number; x1: number; y1: number; min: number } | null

interface Props {
  loaded: Loaded
  answer: Answer | null
  selectedId: string | null
  hoverId: string | null
  dim: boolean
  showPlan: boolean
  mode: '2d' | '3d'
  photo: boolean
  photoStatus: RefObject<HTMLSpanElement | null>
  low: boolean // reduced quality: no AO, no shadows (set by the performance monitor or forced)
  onSlow: (slow: boolean) => void
  focus: Focus
  fitAll: number
  insets: Insets
  tools: { measure: boolean; mpts: Pt[]; mclosed: boolean; peek: boolean; hidden: string[]; cut: number; refit: number }
  onMeasure: (pts: Pt[], closed: boolean) => void
  onSelect: (id: string | null) => void
  onHover: (id: string | null) => void
  overlay: RefObject<HTMLDivElement | null> // DOM layer over the canvas; Html labels portal here (fiber 9 + React 19)
}

export function Scene({ loaded, answer, selectedId, hoverId, dim, showPlan, mode, photo, photoStatus, low, onSlow, focus, fitAll, insets, tools, onMeasure, onSelect, onHover, overlay }: Props) {
  const drawing = loaded.drawing
  const max = Math.max(drawing.width, drawing.height)
  const L = max / 2500 // height step between flat layers, so they never z-fight
  const tex = useDrawingTexture(drawing)
  const hatch = useHatch(drawing)
  // 3D geometry (and a scan's image walls) only once 3D is first asked for: opening a sheet in 2D stays fast
  const [want3d, setWant3d] = useState(mode === '3d')
  useEffect(() => { if (mode === '3d') setWant3d(true) }, [mode])
  const wallH = useMemo(() => (want3d ? wallHeight(loaded) : 0.035 * max), [loaded, want3d, max]) // reading walls is lazy work
  const els = answer?.status === 'answered' ? answer.elements : []
  // peek (held Space / Original button) shows the untouched drawing; legend rows can switch groups off
  const shown = tools.peek ? [] : els.filter((e) => !e.group || !tools.hidden.includes(e.group))
  const tier = answer?.tier ?? null
  const lift = useRef<Lift>({ v: 0 })
  // in 3D with nothing selected, frame the building (walls), not the whole paper with its title block
  const home = useMemo(() => {
    if (!want3d) return null
    const ws = loaded.context.walls
    if (ws.length < 12) return null // too few walls to be a building: frame the whole sheet
    const b = bbox(ws.flat())
    if (Math.hypot(b.w, b.h) < 0.25 * Math.hypot(drawing.width, drawing.height)) return null
    return { x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1, min: 0 }
  }, [loaded, want3d])
  const lifted = useLifted(lift)
  const building = useBuilding(loaded, want3d)

  // double-click zooms in towards the spot; measure clicks snap to the drawing's own corners (rooms, evidence, walls)
  const controls = useThree((st) => st.controls) as unknown as CameraControlsImpl | null
  const camera = useThree((st) => st.camera) as THREE.PerspectiveCamera, size = useThree((st) => st.size)
  const zoomAt = (p: THREE.Vector3) => {
    if (!controls) return
    controls.moveTo(p.x, 0, p.z, true)
    controls.dollyTo(Math.max(controls.minDistance, controls.distance * 0.45), true)
  }
  const snapPts = useMemo(() => {
    if (!tools.measure) return [] as Pt[]
    const pts: Pt[] = []
    for (const r of loaded.context.rooms) pts.push(...r.polygon)
    for (const e of els) pts.push(...e.polygon)
    for (const e of loaded.plan) pts.push(...e.polygon)
    if (!loaded.context.wallsDisjoint) for (const w of loaded.context.walls) pts.push(...w) // exact walls only (scan walls are lazy work)
    return pts
  }, [tools.measure, loaded, els])
  const addPoint = (wp: THREE.Vector3) => {
    let p: Pt = [wp.x + drawing.width / 2, wp.z + drawing.height / 2]
    const wpp = controls ? (2 * controls.distance * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2)) / size.height : 1
    const r = 10 * wpp // 10 screen px
    const first = tools.mpts[0]
    if (!tools.mclosed && tools.mpts.length >= 3 && Math.hypot(p[0] - first[0], p[1] - first[1]) < r) { onMeasure(tools.mpts, true); return }
    let best = r
    for (const q of snapPts) { const d = Math.hypot(q[0] - p[0], q[1] - p[1]); if (d < best) { best = d; p = [q[0], q[1]] } }
    onMeasure(tools.mclosed ? [p] : [...tools.mpts, p], false)
  }

  useFrame((_, dt) => {
    const t = mode === '3d' ? 1 : 0, l = lift.current
    l.v = Math.abs(l.v - t) < 1e-3 ? t : THREE.MathUtils.damp(l.v, t, 3.2, dt)
  })

  return (
    <>
      <CameraControls makeDefault />
      <CameraRig drawing={drawing} focus={focus} fitAll={fitAll} mode={mode} home={home} insets={insets} refit={tools.refit} />
      <Lighting drawing={drawing} active={lifted && !low} catcherY={2.5 * L} />
      {lifted && !photo && !low && <Effects wallH={wallH} />}
      {/* 3D only: if the frame rate stays low, drop AO, shadows and resolution (2D is cheap and never degrades) */}
      {lifted && !photo && <PerformanceMonitor flipflops={3} onDecline={() => onSlow(true)} onIncline={() => onSlow(false)} onFallback={() => onSlow(true)} />}
      {lifted && photo && <Photo loaded={loaded} b={building} tex={tex} wallH={wallH * tools.cut} answer={answer} baseY={0.5 * L} status={photoStatus} />}

      {/* the original drawing: always visible */}
      <mesh rotation-x={-Math.PI / 2} onClick={(e) => { e.stopPropagation(); if (e.delta > CLICK_PX) return; if (tools.measure) addPoint(e.point); else onSelect(null) }}
        onDoubleClick={(e) => { if (!tools.measure) { e.stopPropagation(); zoomAt(e.point) } }}>
        <planeGeometry args={[drawing.width, drawing.height]} />
        <meshBasicMaterial map={tex} toneMapped={false} />
      </mesh>

      {showPlan && !answer && !tools.peek && loaded.plan.map((el) => <PlanMark key={el.id} el={el} drawing={drawing} y={L} />)}

      {dim && shown.length > 0 && (
        <mesh rotation-x={-Math.PI / 2} position-y={2 * L} renderOrder={2} raycast={() => null}>
          <planeGeometry args={[drawing.width, drawing.height]} />
          <meshBasicMaterial color="#f4f5f7" transparent opacity={0.66} depthWrite={false} toneMapped={false} /> {/* light veil: the rest fades back */}
        </mesh>
      )}

      {want3d && <Floors loaded={loaded} wallH={wallH} lift={lift} y={1.5 * L} />}
      <Building b={building} tex={tex} wallH={wallH * tools.cut} lift={lift} y={0.5 * L} />

      {shown.map((el) => (
        <Highlight key={el.id} el={el} drawing={drawing} tex={tex} hatch={hatch} tier={tier} measure={answer?.measure ?? 'other'} crowded={shown.length > 12} glyphs={shown.length <= 24} L={L} wallH={wallH} lift={lift}
          selected={el.id === selectedId} hovered={el.id === hoverId} interactive={!tools.measure}
          onSelect={onSelect} onHover={onHover} onZoom={zoomAt} overlay={overlay} />
      ))}
      {tools.mpts.length > 0 && <MeasureLayer drawing={drawing} pts={tools.mpts} closed={tools.mclosed} L={L} overlay={overlay} />}
    </>
  )
}

function PlanMark({ el, drawing, y }: { el: Element; drawing: Drawing; y: number }) {
  const pts = useMemo(() => {
    const w = el.polygon.map((p) => toWorld(drawing, p))
    return [...w, w[0]].map(([x, z]) => new THREE.Vector3(x, y, z))
  }, [el, drawing, y])
  const dashed = el.type === 'floor' || el.type === 'apartment'
  const dash = Math.max(drawing.width, drawing.height) / 120
  return <Line points={pts} color={KIND_COLOR[el.type] ?? '#475569'} lineWidth={1.5} transparent opacity={0.75}
    dashed={dashed} dashSize={dash} gapSize={dash * 0.6} raycast={() => null} />
}

// A vertical strip along a line (shape space, z 0..1): baseboards in 3D. Closed for room outlines.
function ribbon(d: Drawing, pts: Pt[], closed: boolean) {
  const P = pts.map((p) => toShape(d, p)), n = closed ? P.length : P.length - 1, pos: number[] = []
  for (let i = 0; i < n; i++) {
    const a = P[i], b = P[(i + 1) % P.length]
    pos.push(a.x, a.y, 0, b.x, b.y, 0, b.x, b.y, 1, a.x, a.y, 0, b.x, b.y, 1, a.x, a.y, 1)
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  g.computeVertexNormals()
  return g
}

// The drawn symbol of an evidence item (door leaf + swing, WC, window lines) in the answer colour with a soft glow,
// as a texture over exactly the sheet area it came from. Built after the first frame; null = keep the plain box style.
function useInk(d: Drawing, el: Element, on: boolean, color: string) {
  const [ink, setInk] = useState<{ tex: THREE.Texture; w: number; h: number; x: number; z: number } | null>(null)
  useEffect(() => {
    if (!on) return
    let live = true, t: THREE.Texture | null = null
    inkOf(d, el, el.cls as Cls, color).then((r) => {
      if (!live || !r) return
      t = new THREE.CanvasTexture(r.canvas)
      t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4
      const [x, z] = toWorld(d, [(r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2])
      setInk({ tex: t, w: r.x1 - r.x0, h: r.y1 - r.y0, x, z })
    }).catch(() => {})
    return () => { live = false; t?.dispose(); setInk(null) }
  }, [d, el, on, color])
  return ink
}

function Highlight({ el, drawing, tex, hatch, tier, measure, crowded, glyphs, L, wallH, lift, selected, hovered, interactive, onSelect, onHover, onZoom, overlay }: {
  el: Element; drawing: Drawing; tex: THREE.Texture; hatch: THREE.Texture; tier: Tier | null; measure: Answer['measure']; crowded: boolean; glyphs: boolean
  L: number; wallH: number; lift: RefObject<Lift>
  selected: boolean; hovered: boolean; interactive: boolean; onSelect: (id: string) => void; onHover: (id: string | null) => void
  onZoom: (p: THREE.Vector3) => void; overlay: RefObject<HTMLDivElement | null>
}) {
  const pts = outline(el)
  const open = el.geometry === 'polyline'
  const linear = open || measure === 'length' // lengths (baseboards, perimeters, pipes): outlines, not filled areas
  const pipe = open && el.diameterPx != null
  const area = !open
  const shape = useMemo(() => (area ? shapeOf(drawing, pts) : null), [el, drawing])
  const geo = useMemo(() => (shape ? drawingUV(new THREE.ShapeGeometry(shape), drawing) : null), [shape])
  const vol = useMemo(() => (shape && !linear ? new THREE.ExtrudeGeometry(shape, { depth: 1, bevelEnabled: false, curveSegments: 4 }) : null), [shape, linear])
  const strip = useMemo(() => (linear && !pipe ? ribbon(drawing, pts, !open) : null), [el, drawing, linear, pipe])
  const pipeGeo = useMemo(() => (pipe ? tube(drawing, pts, el.diameterPx! / 2) : null), [el, drawing, pipe])
  useEffect(() => () => { geo?.dispose(); vol?.dispose(); strip?.dispose(); pipeGeo?.dispose() }, [geo, vol, strip, pipeGeo])
  useEffect(() => () => { document.body.style.cursor = '' }, [])
  const line = useMemo(() => {
    const w = pts.map((p) => toWorld(drawing, p))
    return (open ? w : [...w, w[0]]).map(([x, z]) => new THREE.Vector3(x, 0, z))
  }, [el, drawing])
  // honesty tier -> line style: read/measured solid, detected dashed (amber when unsure), estimated hatched
  const detected = tier === 'detected'
  const low = detected && el.confidence != null && el.confidence < LOW_CONF
  const color = low ? LOW : el.color ?? ACCENT
  const active = selected || hovered
  // symbols (doors, WCs, windows, ...): the drawn linework glows; the box stays as a thin, crisp outline
  const glyph = el.cls ? GLYPH[el.cls as Cls] : undefined
  const ink = useInk(drawing, el, !open && !!el.cls && SYMBOL.has(el.cls as Cls), color)
  const sym = !!ink
  // label anchor; a glowing symbol's tag sits just off its box, on the side away from the drawn swing / linework
  let [ax, az] = open ? toWorld(drawing, pts[Math.floor(pts.length / 2)]) : toWorld(drawing, anchor(el))
  let side: 'up' | 'down' | null = null
  if (ink) {
    const b = bbox(pts), [bx, bz0] = toWorld(drawing, [b.cx, b.y0]), bz1 = bz0 + b.h
    const inkUp = bz0 - (ink.z - ink.h / 2) > ink.z + ink.h / 2 - bz1 + 1e-6
    side = inkUp ? 'down' : 'up'
    ax = bx; az = inkUp ? bz1 : bz0
  }
  const dash = Math.max(drawing.width, drawing.height) / 160
  // count numbers always; per-item values only while there is room (else on hover / selection; the legend has totals)
  const tag = el.label ?? (crowded && !active ? null : el.valueText ?? null)
  const events = !interactive ? { raycast: () => null } : {
    onDoubleClick: (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onZoom(e.point) },
    onClick: (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); if (e.delta <= CLICK_PX) onSelect(el.id) },
    onPointerOver: (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); onHover(el.id); document.body.style.cursor = 'pointer' },
    onPointerOut: () => { onHover(null); document.body.style.cursor = '' },
  }

  // 3D: areas grow a translucent volume (slabs: their printed thickness), lengths a baseboard strip, pipes float at
  // mid height; the outline rides on top and the tag floats above on a pin
  const volRef = useRef<THREE.Mesh>(null), stripRef = useRef<THREE.Mesh>(null), pipeRef = useRef<THREE.Group>(null)
  const topRef = useRef<THREE.Group>(null), tagRef = useRef<THREE.Group>(null), pinRef = useRef<THREE.Group>(null)
  useFrame(() => {
    const v = lift.current?.v ?? 0
    const top = linear ? (pipe ? 0 : v * wallH * 0.12) : v * (el.thicknessPx ?? wallH * 0.45)
    if (volRef.current) { volRef.current.visible = v > 0.01; volRef.current.scale.z = Math.max(top, 0.001) }
    if (stripRef.current) { stripRef.current.visible = v > 0.01; stripRef.current.scale.z = Math.max(top, 0.001) }
    if (pipeRef.current) pipeRef.current.position.y = 5 * L + v * wallH * 0.55
    if (topRef.current) topRef.current.position.y = 5 * L + (pipe ? v * wallH * 0.55 : top)
    if (tagRef.current) tagRef.current.position.y = 6 * L + v * wallH * 1.25
    if (pinRef.current) { pinRef.current.visible = v > 0.01; pinRef.current.scale.y = Math.max(v * wallH * 1.25, 0.001) }
  })

  return (
    <group>
      {geo && (
        <mesh geometry={geo} rotation-x={-Math.PI / 2} position-y={3 * L} {...events}>
          <meshBasicMaterial map={tex} toneMapped={false} />
        </mesh>
      )}
      {geo && !linear && (
        <mesh geometry={geo} rotation-x={-Math.PI / 2} position-y={4 * L} renderOrder={5} raycast={() => null}>
          {tier === 'estimated'
            ? <meshBasicMaterial color={color} map={hatch} transparent opacity={active ? 0.55 : 0.35} depthWrite={false} toneMapped={false} />
            : <meshBasicMaterial color={color} transparent opacity={sym ? (active ? 0.12 : 0.04) : active ? 0.32 : 0.14} depthWrite={false} toneMapped={false} />}
        </mesh>
      )}
      {ink && (
        <mesh position={[ink.x, 4.5 * L, ink.z]} rotation-x={-Math.PI / 2} renderOrder={7} raycast={() => null}>
          <planeGeometry args={[ink.w, ink.h]} />
          <meshBasicMaterial map={ink.tex} transparent opacity={active ? 1 : 0.9} depthWrite={false} toneMapped={false} />
        </mesh>
      )}
      {vol && (
        <mesh ref={volRef} geometry={vol} rotation-x={-Math.PI / 2} position-y={4 * L} visible={false} renderOrder={6} raycast={() => null}>
          {el.thicknessPx != null // slabs: solid plates at their printed thickness
            ? <meshStandardMaterial color={color} roughness={0.8} emissive={color} emissiveIntensity={active ? 0.35 : 0.15} />
            : <meshBasicMaterial color={color} transparent opacity={active ? 0.38 : 0.2} depthWrite={false} side={THREE.DoubleSide} toneMapped={false} />}
        </mesh>
      )}
      {strip && (
        <mesh ref={stripRef} geometry={strip} rotation-x={-Math.PI / 2} position-y={4 * L} visible={false} renderOrder={6} raycast={() => null}>
          <meshBasicMaterial color={color} transparent opacity={active ? 0.95 : 0.8} side={THREE.DoubleSide} toneMapped={false} />
        </mesh>
      )}
      {pipeGeo && (
        <group ref={pipeRef}>
          <mesh geometry={pipeGeo} {...events}>
            <meshStandardMaterial color={color} roughness={0.35} metalness={0.4} emissive={color} emissiveIntensity={active ? 0.5 : 0.25} />
          </mesh>
        </group>
      )}
      <group ref={topRef}>
        <Line points={line} color={color} lineWidth={sym ? (active ? 3 : 1.5) : active ? (linear ? 6 : 4) : linear ? 4.5 : 2.5} dashed={detected} dashSize={dash} gapSize={dash * 0.6}
          {...(open ? events : { raycast: () => null })} />
      </group>
      <group ref={pinRef} position={[ax, 5 * L, az]} visible={false}>
        <Line points={[[0, 0, 0], [0, 1, 0]]} color={color} lineWidth={1.5} transparent opacity={0.8} raycast={() => null} />
      </group>
      {tag && (
        <group ref={tagRef} position={[ax, 6 * L, az]}>
          <Html portal={overlay as RefObject<HTMLElement>} center zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
            <div className={`dv-tag${active ? ' dv-tag-active' : ''}${low ? ' dv-tag-low' : ''}${side ? ` dv-tag-${side}` : ''}`} style={low ? undefined : { background: color }}>
              {glyph && (glyphs || active) && <svg className="dv-glyph" viewBox="0 0 16 16" aria-hidden="true"><path d={glyph} /></svg>}{tag}
            </div>
          </Html>
        </group>
      )}
    </group>
  )
}

// The measure tool's drawing: the line (closed into an area when finished), a dot per point, a length per segment.
function MeasureLayer({ drawing, pts, closed, L, overlay }: { drawing: Drawing; pts: Pt[]; closed: boolean; L: number; overlay: RefObject<HTMLDivElement | null> }) {
  const sc = measureScale(drawing)
  const w = pts.map((p) => toWorld(drawing, p))
  const line = (closed ? [...w, w[0]] : w).map(([x, z]) => new THREE.Vector3(x, 7 * L, z))
  const fill = useMemo(() => (closed && pts.length >= 3 ? new THREE.ShapeGeometry(shapeOf(drawing, pts)) : null), [closed, pts, drawing])
  useEffect(() => () => fill?.dispose(), [fill])
  const segs = pts.map((p, i) => [p, pts[(i + 1) % pts.length]] as const).slice(0, closed ? pts.length : pts.length - 1)
  const fmt = (v: number) => (sc.unit === 'px' ? Math.round(v).toString() : v.toFixed(2))
  const portal = overlay as RefObject<HTMLElement>
  return (
    <group>
      {fill && (
        <mesh geometry={fill} rotation-x={-Math.PI / 2} position-y={6.5 * L} renderOrder={8} raycast={() => null}>
          <meshBasicMaterial color="#2f6fde" transparent opacity={0.14} depthWrite={false} toneMapped={false} />
        </mesh>
      )}
      {line.length >= 2 && <Line points={line} color="#2f6fde" lineWidth={2.5} raycast={() => null} />}
      {w.map(([x, z], i) => (
        <Html key={`p${i}`} portal={portal} position={[x, 7 * L, z]} center zIndexRange={[30, 0]} style={{ pointerEvents: 'none' }}><div className="dv-mdot" /></Html>
      ))}
      {segs.map(([a, b], i) => {
        const [x, z] = toWorld(drawing, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])
        return (
          <Html key={`s${i}`} portal={portal} position={[x, 7 * L, z]} center zIndexRange={[30, 0]} style={{ pointerEvents: 'none' }}>
            <div className="dv-mlabel">{fmt(Math.hypot(b[0] - a[0], b[1] - a[1]) * sc.f)} {sc.unit === 'px' ? 'px' : sc.unit === 'm' ? 'm' : 'mm'}</div>
          </Html>
        )
      })}
    </group>
  )
}

export function measureTotals(d: Drawing, pts: Pt[], closed: boolean) {
  const sc = measureScale(d)
  let len = 0
  for (let i = 0; i + 1 < pts.length; i++) len += Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
  if (closed && pts.length >= 3) len += Math.hypot(pts[0][0] - pts[pts.length - 1][0], pts[0][1] - pts[pts.length - 1][1])
  return { length: len * sc.f, area: closed && pts.length >= 3 ? Math.abs(ringArea(pts)) * sc.f * sc.f : null, sc }
}

// Camera: one perspective camera for both modes. Looking straight down, a flat sheet shows exactly as in a 2D viewer;
// 3D tilts the same camera while the walls rise. Fits `focus` (sheet-pixel rect) smoothly; user input takes over.
function CameraRig({ drawing, focus, fitAll, mode, home, insets, refit }: { drawing: Drawing; focus: Focus; fitAll: number; mode: '2d' | '3d'; home: Focus; insets: Insets; refit: number }) {
  const controls = useThree((s) => s.controls) as unknown as CameraControlsImpl | null
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera
  const size = useThree((s) => s.size)
  const first = useRef(true)
  const lastFitAll = useRef(fitAll)
  const max = Math.max(drawing.width, drawing.height)

  useEffect(() => { // input mapping and angle limits per mode
    if (!controls) return
    controls.dollyToCursor = true
    controls.smoothTime = 0.45
    controls.draggingSmoothTime = 0.08
    controls.minDistance = max * 0.02
    controls.maxDistance = max * 12
    const b = controls.mouseButtons, t = controls.touches
    if (mode === '2d') {
      b.left = ACTION.TRUCK; b.right = ACTION.TRUCK; b.middle = ACTION.DOLLY; b.wheel = ACTION.DOLLY
      t.one = ACTION.TOUCH_TRUCK; t.two = ACTION.TOUCH_DOLLY_TRUCK; t.three = ACTION.TOUCH_TRUCK
      controls.minPolarAngle = 0; controls.maxPolarAngle = 0
      controls.minAzimuthAngle = 0; controls.maxAzimuthAngle = 0
      controls.rotateTo(0, 0, !first.current)
    } else {
      b.left = ACTION.ROTATE; b.right = ACTION.TRUCK; b.middle = ACTION.DOLLY; b.wheel = ACTION.DOLLY
      t.one = ACTION.TOUCH_ROTATE; t.two = ACTION.TOUCH_DOLLY_TRUCK; t.three = ACTION.TOUCH_TRUCK
      controls.minPolarAngle = 0; controls.maxPolarAngle = 1.3
      controls.minAzimuthAngle = -Infinity; controls.maxAzimuthAngle = Infinity
      controls.rotateTo(TILT.azimuth, TILT.polar, !first.current)
    }
  }, [controls, mode, max])

  useEffect(() => { // fit the focus rect (or the whole sheet)
    if (!controls) return
    const all = fitAll !== lastFitAll.current
    lastFitAll.current = fitAll
    const f = all ? null : focus
    const r = f ?? (mode === '3d' && home ? home : { x0: 0, y0: 0, x1: drawing.width, y1: drawing.height })
    // keep some drawing around small elements so there's context to verify against
    const pad = f ? 1.5 : 1.06, min = f ? max * f.min : 0
    const w = Math.max((r.x1 - r.x0) * pad, min), h = Math.max((r.y1 - r.y0) * pad, min)
    const [x, z] = toWorld(drawing, [(r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2])
    // fit into the part of the view the cards leave free, then shift the framing so it is centred there
    const fw = Math.max(size.width - insets.l - insets.r, size.width * 0.4), fh = Math.max(size.height - insets.t - insets.b, size.height * 0.4)
    const vf = THREE.MathUtils.degToRad(camera.fov), tv = Math.tan(vf / 2), th = tv * (size.width / size.height)
    const dist = Math.max((h / 2 / tv) * (size.height / fh), (w / 2 / th) * (size.width / fw)) * (mode === '3d' ? 1.3 : 1)
    const wpp = (2 * dist * tv) / size.height // world units per screen pixel at the target
    const anim = !first.current
    first.current = false
    controls.moveTo(x, 0, z, anim)
    controls.dollyTo(dist, anim)
    controls.setFocalOffset((-(insets.l - insets.r) / 2) * wpp, ((insets.t - insets.b) / 2) * wpp, 0, anim)
  }, [focus, fitAll, drawing, size.width, size.height, controls, mode, home, insets, refit])

  useFrame(() => { // near/far follow the distance so the thin flat layers keep their depth order at every zoom
    if (!controls) return
    const d = controls.distance, near = Math.max(0.5, d / 200), far = d * 6 + max * 3
    if (Math.abs(camera.near - near) / near > 0.15 || Math.abs(camera.far - far) / far > 0.15) {
      camera.near = near; camera.far = far; camera.updateProjectionMatrix()
    }
  })
  return null
}

export const focusOf = (els: Element[], min = 0.12): Focus => {
  if (!els.length) return null
  const b = bbox(els.flatMap((e) => outline(e)))
  return { x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1, min }
}
