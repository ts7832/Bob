import { useEffect, useMemo, useRef, type RefObject } from 'react'
import * as THREE from 'three'
import { useFrame, useThree } from '@react-three/fiber'
import { Html, Line, MapControls, OrthographicCamera } from '@react-three/drei'
import type { Answer, Drawing, Element, Loaded, Tier } from './types'
import { anchor, bbox, outline, shapeOf, toWorld } from './geom'

export const ACCENT = '#ff6a00'
const LOW = '#e0a100' // low-confidence detections
const LOW_CONF = 0.6

// Colours per element type for the faint "found on this sheet" layer (same palette as demo/index.html).
const KIND_COLOR: Record<string, string> = {
  door: '#2563eb', window: '#0d9488', wc: '#9333ea', sink: '#db2777', bathtub: '#0891b2', shower: '#4f46e5',
  sauna_stove: '#b45309', sauna_bench: '#a16207', bedroom: '#3b82f6', kitchen: '#eab308', bathroom: '#10b981',
  wc_room: '#a855f7', balcony: '#84cc16', sauna_room: '#ef4444', living_room: '#f97316', dining: '#d97706', room: '#6366f1',
  entry: '#0ea5e9', storage: '#78716c', utility: '#14b8a6', dressing_room: '#ec4899', terrace: '#65a30d', garage: '#475569',
  other_room: '#94a3b8', wall: '#374151', apartment: '#d64545', floor: '#111827',
}

// Shape geometry whose UVs sample the drawing texture at the same spot, so a highlight
// re-shows the original drawing undimmed instead of painting over it.
function drawingUV(geo: THREE.BufferGeometry, d: Drawing) {
  const pos = geo.attributes.position, uv = geo.attributes.uv
  for (let i = 0; i < pos.count; i++) uv.setXY(i, (pos.getX(i) + d.width / 2) / d.width, (pos.getY(i) + d.height / 2) / d.height)
  uv.needsUpdate = true
  return geo
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

// min: smallest view box as a fraction of the sheet's long side, so there is always context to verify against
export type Focus = { x0: number; y0: number; x1: number; y1: number; min: number } | null

interface Props {
  loaded: Loaded
  answer: Answer | null
  selectedId: string | null
  hoverId: string | null
  dim: boolean
  showPlan: boolean
  focus: Focus
  fitAll: number
  onSelect: (id: string | null) => void
  onHover: (id: string | null) => void
  overlay: RefObject<HTMLDivElement | null> // DOM layer over the canvas; Html labels portal here (fiber 9 + React 19)
}

export function Scene({ loaded, answer, selectedId, hoverId, dim, showPlan, focus, fitAll, onSelect, onHover, overlay }: Props) {
  const drawing = loaded.drawing
  const tex = useDrawingTexture(drawing)
  const hatch = useHatch(drawing)
  const els = answer?.status === 'answered' ? answer.elements : []
  const tier = answer?.tier ?? null

  return (
    <>
      <OrthographicCamera makeDefault position={[0, 5000, 0]} up={[0, 0, -1]} near={1} far={20000} />
      <MapControls makeDefault enableRotate={false} enableDamping={false} zoomToCursor minZoom={0.01} maxZoom={40} />
      <CameraRig drawing={drawing} focus={focus} fitAll={fitAll} />

      {/* the original drawing: always visible */}
      <mesh rotation-x={-Math.PI / 2} onClick={(e) => { e.stopPropagation(); onSelect(null) }}>
        <planeGeometry args={[drawing.width, drawing.height]} />
        <meshBasicMaterial map={tex} toneMapped={false} />
      </mesh>

      {showPlan && !els.length && loaded.plan.map((el) => <PlanMark key={el.id} el={el} drawing={drawing} />)}

      {dim && els.length > 0 && (
        <mesh rotation-x={-Math.PI / 2} position-y={0.1} raycast={() => null}>
          <planeGeometry args={[drawing.width, drawing.height]} />
          <meshBasicMaterial color="#0b1020" transparent opacity={0.55} depthWrite={false} toneMapped={false} />
        </mesh>
      )}

      {els.map((el) => (
        <Highlight key={el.id} el={el} drawing={drawing} tex={tex} hatch={hatch} tier={tier}
          selected={el.id === selectedId} hovered={el.id === hoverId}
          onSelect={onSelect} onHover={onHover} overlay={overlay} />
      ))}
    </>
  )
}

function PlanMark({ el, drawing }: { el: Element; drawing: Drawing }) {
  const pts = useMemo(() => {
    const w = el.polygon.map((p) => toWorld(drawing, p))
    return [...w, w[0]].map(([x, z]) => new THREE.Vector3(x, 0.05, z))
  }, [el, drawing])
  const dashed = el.type === 'floor' || el.type === 'apartment'
  const dash = Math.max(drawing.width, drawing.height) / 120
  return <Line points={pts} color={KIND_COLOR[el.type] ?? '#475569'} lineWidth={1.5} transparent opacity={0.75}
    dashed={dashed} dashSize={dash} gapSize={dash * 0.6} raycast={() => null} />
}

function Highlight({ el, drawing, tex, hatch, tier, selected, hovered, onSelect, onHover, overlay }: {
  el: Element; drawing: Drawing; tex: THREE.Texture; hatch: THREE.Texture; tier: Tier | null
  selected: boolean; hovered: boolean; onSelect: (id: string) => void; onHover: (id: string | null) => void
  overlay: RefObject<HTMLDivElement | null>
}) {
  const pts = outline(el)
  const geo = useMemo(() => drawingUV(new THREE.ShapeGeometry(shapeOf(drawing, pts)), drawing), [el, drawing])
  useEffect(() => () => geo.dispose(), [geo])
  const line = useMemo(() => {
    const w = pts.map((p) => toWorld(drawing, p))
    return [...w, w[0]].map(([x, z]) => new THREE.Vector3(x, 0.4, z))
  }, [el, drawing])
  const [ax, az] = toWorld(drawing, anchor(el))
  // honesty tier -> line style: read/measured solid, detected dashed (amber when unsure), estimated hatched
  const detected = tier === 'detected'
  const low = detected && el.confidence != null && el.confidence < LOW_CONF
  const color = low ? LOW : ACCENT
  const active = selected || hovered
  const dash = Math.max(drawing.width, drawing.height) / 160

  return (
    <group>
      <mesh geometry={geo} rotation-x={-Math.PI / 2} position-y={0.2}
        onClick={(e) => { e.stopPropagation(); onSelect(el.id) }}
        onPointerOver={(e) => { e.stopPropagation(); onHover(el.id); document.body.style.cursor = 'pointer' }}
        onPointerOut={() => { onHover(null); document.body.style.cursor = '' }}>
        <meshBasicMaterial map={tex} toneMapped={false} />
      </mesh>
      <mesh geometry={geo} rotation-x={-Math.PI / 2} position-y={0.3} raycast={() => null}>
        {tier === 'estimated'
          ? <meshBasicMaterial color={color} map={hatch} transparent opacity={active ? 0.55 : 0.35} depthWrite={false} toneMapped={false} />
          : <meshBasicMaterial color={color} transparent opacity={active ? 0.32 : 0.14} depthWrite={false} toneMapped={false} />}
      </mesh>
      <Line points={line} color={color} lineWidth={active ? 4 : 2.5} dashed={detected} dashSize={dash} gapSize={dash * 0.6} raycast={() => null} />
      {el.label && (
        <Html portal={overlay as RefObject<HTMLElement>} position={[ax, 1, az]} center zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
          <div className={`dv-tag${active ? ' dv-tag-active' : ''}${low ? ' dv-tag-low' : ''}`}>{el.label}</div>
        </Html>
      )}
    </group>
  )
}

// Fits the camera to `focus` (sheet-pixel rect) with a smooth ease; user input cancels it.
function CameraRig({ drawing, focus, fitAll }: { drawing: Drawing; focus: Focus; fitAll: number }) {
  const { camera, size, controls } = useThree() as unknown as {
    camera: THREE.OrthographicCamera; size: { width: number; height: number }; controls: any
  }
  const goal = useRef<{ x: number; z: number; zoom: number } | null>(null)
  const first = useRef(true)
  const lastFitAll = useRef(fitAll)

  useEffect(() => {
    if (!controls) return // controls attach a frame late; fitting before that gets overridden
    const all = fitAll !== lastFitAll.current
    lastFitAll.current = fitAll
    const f = all ? null : focus
    const r = f ?? { x0: 0, y0: 0, x1: drawing.width, y1: drawing.height }
    // keep some drawing around small elements so there's context to verify against
    const pad = f ? 1.5 : 1.06, min = f ? Math.max(drawing.width, drawing.height) * f.min : 0
    const w = Math.max((r.x1 - r.x0) * pad, min), h = Math.max((r.y1 - r.y0) * pad, min)
    const [x, z] = toWorld(drawing, [(r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2])
    const zoom = Math.min(size.width / w, size.height / h)
    goal.current = { x, z, zoom }
    if (first.current) { // jump on first frame, animate afterwards
      first.current = false
      camera.position.set(x, 5000, z); camera.zoom = zoom; camera.updateProjectionMatrix()
      controls.target.set(x, 0, z); controls.update()
      goal.current = null
    }
  }, [focus, fitAll, drawing, size.width, size.height, controls])

  useEffect(() => {
    if (!controls) return
    const stop = () => { goal.current = null }
    controls.addEventListener('start', stop)
    return () => controls.removeEventListener('start', stop)
  }, [controls])

  useFrame((_, dt) => {
    const g = goal.current
    if (!g) return
    const k = 1 - Math.exp(-dt * 7)
    camera.position.x += (g.x - camera.position.x) * k
    camera.position.z += (g.z - camera.position.z) * k
    camera.zoom += (g.zoom - camera.zoom) * k
    camera.updateProjectionMatrix()
    controls?.target.set(camera.position.x, 0, camera.position.z)
    controls?.update()
    const eps = Math.max(drawing.width, drawing.height) / 4000
    if (Math.abs(g.zoom - camera.zoom) / g.zoom < 0.002 && Math.abs(g.x - camera.position.x) < eps && Math.abs(g.z - camera.position.z) < eps) goal.current = null
  })
  return null
}

export const focusOf = (els: Element[], min = 0.12): Focus => {
  if (!els.length) return null
  const b = bbox(els.flatMap((e) => outline(e)))
  return { x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1, min }
}
