// The realistic 3D building: walls with window sills, glass and lintels, door lintels, fixtures, studio lighting with
// soft shadows, ambient occlusion. Everything is built from the drawing's own geometry and only shown when lifted.
// Heights are illustrative (fractions of one storey); nothing here feeds an answer.
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import * as THREE from 'three'
import { useFrame, useThree } from '@react-three/fiber'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import { EffectComposer, EffectPass, RenderPass, SMAAEffect } from 'postprocessing'
import { N8AOPostPass } from 'n8ao'
import type { Drawing, Loaded, Pt } from './types'
import { obb, obbRect, shapeOf, shapesFromRings } from './geom'
import { floorKind, floorTexture, TILE_M, type FloorKind } from './floors'
import { buildFixtures, fixtureMaterials } from './fixtures3d'

export type Lift = { v: number } // 0 = the flat sheet, 1 = full 3D

// storey fractions (2.6 m storey): window sill 0.9 m, window head 2.1 m, door head 2.1 m
const SILL = 0.9 / 2.6, HEAD = 2.1 / 2.6, DOOR_HEAD = 2.1 / 2.6
const FIXTURE_M: Record<string, number> = {
  wc: 0.42, washbasin: 0.85, sink: 0.85, kitchen_unit: 0.9, bathtub: 0.55, shower: 0.05,
  sauna_bench: 0.45, sauna_stove: 0.7, IfcSanitaryTerminal: 0.6, IfcFurnishingElement: 0.75, IfcFurniture: 0.75,
}

// UVs that sample the drawing texture at the same spot: wall tops show the original drawing
export function drawingUV(geo: THREE.BufferGeometry, d: Drawing) {
  const pos = geo.attributes.position, uv = geo.attributes.uv
  for (let i = 0; i < pos.count; i++) uv.setXY(i, (pos.getX(i) + d.width / 2) / d.width, (pos.getY(i) + d.height / 2) / d.height)
  uv.needsUpdate = true
  return geo
}

const extrude = (s: THREE.Shape, depth: number, z = 0, bevel = 0) => {
  const g = new THREE.ExtrudeGeometry(s, bevel > 0
    ? { depth, bevelEnabled: true, bevelSize: bevel, bevelThickness: depth * 0.12, bevelSegments: 2, curveSegments: 4 }
    : { depth, bevelEnabled: false, curveSegments: 4 })
  if (z) g.translate(0, 0, z)
  return g
}

// Split an ExtrudeGeometry by material group (0 = caps, 1 = sides) and merge all of them: two draw calls in total.
function mergeCapsSides(geos: THREE.BufferGeometry[]): [THREE.BufferGeometry, THREE.BufferGeometry] | null {
  if (!geos.length) return null
  const part = (g: THREE.BufferGeometry, mi: number) => {
    const out = new THREE.BufferGeometry()
    for (const name of ['position', 'normal', 'uv', 'center'] as const) {
      const at = g.getAttribute(name) as THREE.BufferAttribute, n = at.itemSize, src = at.array as Float32Array
      const arr: number[] = []
      for (const gr of g.groups) if (gr.materialIndex === mi) for (let i = gr.start * n; i < (gr.start + gr.count) * n; i++) arr.push(src[i])
      out.setAttribute(name, new THREE.Float32BufferAttribute(arr, n))
    }
    return out
  }
  const res: [THREE.BufferGeometry, THREE.BufferGeometry] = [mergeGeometries(geos.map((g) => part(g, 0))), mergeGeometries(geos.map((g) => part(g, 1)))]
  geos.forEach((g) => g.dispose())
  return res
}

// Each wall piece carries its own centre, so the cutaway lowers a whole wall evenly (no sloped tops)
function centred(g: THREE.BufferGeometry) {
  g.computeBoundingBox()
  const c = new THREE.Vector3(); g.boundingBox!.getCenter(c)
  const n = g.attributes.position.count, a = new Float32Array(2 * n)
  for (let i = 0; i < n; i++) { a[2 * i] = c.x; a[2 * i + 1] = c.y }
  g.setAttribute('center', new THREE.Float32BufferAttribute(a, 2))
  return g
}

// Cutaway ("dollhouse"): walls on the camera's side of the building drop low, the back walls stay full, so every room
// can be seen into from any angle. Done in the vertex shader (and the shadow depth pass), so it follows the camera.
export type CutU = { uCam: { value: THREE.Vector2 }; uMid: { value: THREE.Vector2 }; uRad: { value: number }; uLow: { value: number } }
export function cutaway<M extends THREE.Material>(m: M, u: CutU, perWall: boolean): M {
  const prev = m.onBeforeCompile
  m.onBeforeCompile = (sh, r) => {
    prev?.call(m, sh, r)
    Object.assign(sh.uniforms, u)
    sh.vertexShader = (perWall ? 'attribute vec2 center;\n' : '') + 'uniform vec2 uCam; uniform vec2 uMid; uniform float uRad; uniform float uLow;\n' +
      sh.vertexShader.replace('#include <begin_vertex>', `#include <begin_vertex>
      { vec2 c = ${perWall ? 'center' : 'transformed.xy'}; float side = dot(c - uMid, uCam) / max(uRad, 1.0);
        transformed.z *= mix(1.0, uLow, smoothstep(-0.05, 0.35, side)); }`)
  }
  m.customProgramCacheKey = () => (perWall ? 'cutw' : 'cutp')
  return m
}

// Keeps the cutaway's camera direction and building centre current (shape space of the 3D groups: y = -world z)
export function useCutaway(loaded: Loaded, on: boolean): CutU {
  const u = useMemo<CutU>(() => ({ uCam: { value: new THREE.Vector2(0, -1) }, uMid: { value: new THREE.Vector2() }, uRad: { value: 1 }, uLow: { value: 1 } }), [])
  useEffect(() => {
    const d = loaded.drawing, ws = loaded.context.wallsDisjoint ? [] : loaded.context.walls // scans: whole sheet (lazy walls)
    let x0 = 0, y0 = 0, x1 = d.width, y1 = d.height
    if (ws.length) { const b = bboxPts(ws.flat()); x0 = b[0]; y0 = b[1]; x1 = b[2]; y1 = b[3] }
    u.uMid.value.set((x0 + x1) / 2 - d.width / 2, d.height / 2 - (y0 + y1) / 2)
    u.uRad.value = Math.hypot(x1 - x0, y1 - y0) / 2
  }, [loaded, u])
  useFrame(({ camera, controls }) => {
    u.uLow.value = on ? 0.3 : 1
    const t = (controls as unknown as { getTarget?: (v: THREE.Vector3) => THREE.Vector3 } | null)?.getTarget?.(new THREE.Vector3()) ?? new THREE.Vector3()
    const dx = camera.position.x - t.x, dz = camera.position.z - t.z, l = Math.hypot(dx, dz)
    if (l > 1e-6) u.uCam.value.set(dx / l, -dz / l)
    else u.uLow.value = 1 // straight down: nothing faces the camera
  })
  return u
}
const bboxPts = (pts: Pt[]) => {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const [x, y] of pts) { if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y }
  return [x0, y0, x1, y1]
}

export type BuildingGeo = ReturnType<typeof useBuilding>

export function useBuilding(loaded: Loaded, enabled = true) {
  return useMemo(() => {
    if (!enabled) return { solid: null, glass: null, fixtures: null } // 2D only so far: build nothing
    const d = loaded.drawing, ctx = loaded.context
    const solid: THREE.BufferGeometry[] = [], glass: THREE.BufferGeometry[] = [], fixtures: THREE.BufferGeometry[] = []
    for (const s of shapesFromRings(d, ctx.walls, ctx.wallsDisjoint)) solid.push(centred(drawingUV(extrude(s, 1), d)))
    for (const o of ctx.openings) {
      if (o.polygon.length < 3) continue
      const b = obb(o.polygon)
      if (b.len < 1 || b.dep < 0.5) continue
      const rect = shapeOf(d, obbRect(b))
      if (o.cls === 'window') {
        solid.push(centred(drawingUV(extrude(rect, SILL), d))) // sill wall
        solid.push(centred(drawingUV(extrude(rect, 1 - HEAD, HEAD), d))) // lintel
        glass.push(centred(extrude(shapeOf(d, obbRect(b, 0.18)), HEAD - SILL, SILL)))
      } else {
        solid.push(centred(drawingUV(extrude(rect, 1 - DOOR_HEAD, DOOR_HEAD), d)))
      }
    }
    // fixtures and door leaves are real models now (fixtures3d.ts, <Fixtures>); this list stays for photo mode's scene
    return {
      solid: mergeCapsSides(solid),
      glass: glass.length ? mergeGeometries(glass) : null,
      fixtures: fixtures.length ? mergeGeometries(fixtures) : null,
    }
  }, [loaded, enabled])
}

export function Building({ b, tex, wallH, lift, y, cut }: { b: BuildingGeo; tex: THREE.Texture; wallH: number; lift: RefObject<Lift>; y: number; cut: CutU }) {
  useEffect(() => () => { b.solid?.forEach((g) => g.dispose()); b.glass?.dispose(); b.fixtures?.dispose() }, [b])
  const mats = useMemo(() => ({
    cap: cutaway(new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }), cut, true), // wall tops: the drawing itself
    wall: cutaway(new THREE.MeshStandardMaterial({ color: '#efebe4', roughness: 0.92, envMapIntensity: 0.9 }), cut, true),
    glass: cutaway(new THREE.MeshStandardMaterial({ color: '#b9d3e2', roughness: 0.04, metalness: 0.1, transparent: true, opacity: 0.38, envMapIntensity: 1.6, depthWrite: false }), cut, true),
    ceramic: new THREE.MeshStandardMaterial({ color: '#f7f7f5', roughness: 0.28, envMapIntensity: 1.1 }),
    depth: cutaway(new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking }), cut, true), // shadows follow the cut
  }), [tex, cut])
  useEffect(() => () => Object.values(mats).forEach((m) => m.dispose()), [mats])
  const group = useRef<THREE.Group>(null)
  useFrame(() => {
    const g = group.current, v = lift.current?.v ?? 0
    if (!g) return
    g.visible = v > 0.002
    g.scale.z = Math.max(v, 0.001) * wallH
  })
  return (
    <group ref={group} rotation-x={-Math.PI / 2} position-y={y} visible={false}>
      {b.solid && <>
        <mesh geometry={b.solid[0]} material={mats.cap} raycast={() => null} />
        <mesh geometry={b.solid[1]} material={mats.wall} customDepthMaterial={mats.depth} castShadow receiveShadow raycast={() => null} />
      </>}
      {b.fixtures && <mesh geometry={b.fixtures} material={mats.ceramic} castShadow receiveShadow raycast={() => null} />}
      {b.glass && <mesh geometry={b.glass} material={mats.glass} raycast={() => null} />}
    </group>
  )
}

// WCs, basins, tubs, showers, kitchen units, sauna benches and stoves, door leaves: modelled from each detected box
// (fixtures3d.ts). Heights are built in sheet px, so the group's z scale is just the lift.
export function Fixtures({ loaded, wallH, lift, y, cut }: { loaded: Loaded; wallH: number; lift: RefObject<Lift>; y: number; cut: CutU }) {
  const geos = useMemo(() => buildFixtures(loaded, wallH), [loaded, wallH])
  useEffect(() => () => geos.forEach((g) => g.dispose()), [geos])
  const mats = useMemo(() => {
    const m = fixtureMaterials()
    cutaway(m.leaf, cut, false); cutaway(m.glass, cut, false) // tall door leaves and shower glass follow the cutaway
    return m
  }, [cut])
  useEffect(() => () => Object.values(mats).forEach((m) => m.dispose()), [mats])
  const group = useRef<THREE.Group>(null)
  useFrame(() => {
    const g = group.current, v = lift.current?.v ?? 0
    if (!g) return
    g.visible = v > 0.002
    g.scale.z = Math.max(v, 0.001)
  })
  return (
    <group ref={group} rotation-x={-Math.PI / 2} position-y={y} visible={false}>
      {[...geos].map(([m, g]) => <mesh key={m} geometry={g} material={mats[m]} castShadow={m !== 'glass'} receiveShadow raycast={() => null} />)}
    </group>
  )
}

// Studio lighting that needs no network: three's RoomEnvironment baked to an environment map, plus a soft key light.
export function Lighting({ drawing, active, catcherY }: { drawing: Drawing; active: boolean; catcherY: number }) {
  const gl = useThree((s) => s.gl), scene = useThree((s) => s.scene)
  const max = Math.max(drawing.width, drawing.height)
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl)
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
    scene.environment = env
    scene.environmentIntensity = 0.7
    return () => { scene.environment = null; env.dispose(); pmrem.dispose() }
  }, [gl, scene])
  const light = useRef<THREE.DirectionalLight>(null)
  useEffect(() => {
    const l = light.current
    if (!l) return
    const c = l.shadow.camera
    c.left = c.bottom = -max * 0.62; c.right = c.top = max * 0.62; c.near = max * 0.1; c.far = max * 4
    c.updateProjectionMatrix()
    l.shadow.mapSize.set(4096, 4096)
    l.shadow.bias = -0.0004
    l.shadow.normalBias = max / 1500
    l.shadow.radius = 3
    l.target.position.set(0, 0, 0)
    l.target.updateMatrixWorld()
  }, [max])
  return (
    <>
      <hemisphereLight args={['#ffffff', '#a39b90', 0.5]} />
      <directionalLight ref={light} position={[-0.55 * max, 1.25 * max, -0.42 * max]} intensity={2.1} color="#fff6ea" castShadow={active} />
      {/* soft shadows on the sheet without changing the drawing: a shadow-only layer just above it */}
      {active && (
        <mesh rotation-x={-Math.PI / 2} position-y={catcherY} receiveShadow renderOrder={3} raycast={() => null}>
          <planeGeometry args={[drawing.width, drawing.height]} />
          <shadowMaterial transparent opacity={0.22} depthWrite={false} />
        </mesh>
      )}
    </>
  )
}

// True while the scene is lifted at all (drives shadows and the effects composer, which 2D never uses).
export function useLifted(lift: RefObject<Lift>) {
  const [on, setOn] = useState(false)
  useFrame(() => {
    const now = (lift.current?.v ?? 0) > 0.002
    if (now !== on) setOn(now)
  })
  return on
}

// Ambient occlusion (N8AO) and SMAA, via pmndrs postprocessing. Mounted only in 3D, where it
// takes over rendering; unmounted, fiber renders the plain scene again, so 2D stays an exact copy of the drawing.
export function Effects({ wallH }: { wallH: number }) {
  const gl = useThree((s) => s.gl), scene = useThree((s) => s.scene), camera = useThree((s) => s.camera), size = useThree((s) => s.size)
  const composer = useMemo(() => {
    const c = new EffectComposer(gl, { frameBufferType: THREE.HalfFloatType })
    c.addPass(new RenderPass(scene, camera))
    const ao = new N8AOPostPass(scene, camera, size.width, size.height)
    ao.configuration.aoRadius = wallH * 0.45
    ao.configuration.distanceFalloff = wallH * 0.15
    ao.configuration.intensity = 2.2
    ao.configuration.halfRes = true
    ao.configuration.transparencyAware = false
    ao.configuration.gammaCorrection = false
    ao.setQualityMode('Medium')
    c.addPass(ao)
    // no tone mapping: it crushed the dark background, and without it the drawing keeps its exact colours in 3D too
    c.addPass(new EffectPass(camera, new SMAAEffect()))
    return c
  }, [gl, scene, camera, wallH])
  useEffect(() => () => composer.dispose(), [composer])
  useEffect(() => { composer.setSize(size.width, size.height) }, [composer, size.width, size.height])
  useFrame((_, dt) => { composer.render(dt) }, 1)
  return null
}

// Realistic floors per room, multiplied over the drawing (white paper takes the floor's colour, printed lines stay dark).
// They fade in with the lift and are hidden in 2D, so the flat view is untouched.
export function Floors({ loaded, wallH, lift, y }: { loaded: Loaded; wallH: number; lift: RefObject<Lift>; y: number }) {
  const d = loaded.drawing
  const pxPerM = d.mPerPx ? 1 / d.mPerPx : wallH / 2.6 // no scale: one storey height = 2.6 m (illustrative)
  const parts = useMemo(() => {
    const by = new Map<FloorKind, THREE.BufferGeometry[]>()
    const fallback: FloorKind = d.kind === 'svg' ? 'stone' : 'wood' // BIM spaces here carry numbers, not uses
    for (const r of loaded.context.rooms) {
      if (r.polygon.length < 3) continue
      const kind = floorKind(r.name, r.type, fallback)
      const g = new THREE.ShapeGeometry(shapeOf(d, r.polygon))
      const pos = g.attributes.position, uv = g.attributes.uv, t = TILE_M[kind] * pxPerM
      for (let i = 0; i < pos.count; i++) uv.setXY(i, pos.getX(i) / t, pos.getY(i) / t)
      by.set(kind, [...(by.get(kind) ?? []), g])
    }
    return [...by].map(([kind, geos]) => ({ kind, geo: mergeGeometries(geos) }))
  }, [loaded, pxPerM])
  useEffect(() => () => parts.forEach((p) => p.geo.dispose()), [parts])
  const fade = useMemo(() => ({ value: 0 }), [])
  const mats = useMemo(() => Object.fromEntries(parts.map(({ kind }) => {
    const m = new THREE.MeshStandardMaterial({
      map: floorTexture(kind), roughness: kind === 'tile' ? 0.35 : kind === 'wood' ? 0.6 : 0.85, envMapIntensity: 0.8,
      transparent: true, depthWrite: false,
      blending: THREE.CustomBlending, blendEquation: THREE.AddEquation, blendSrc: THREE.DstColorFactor, blendDst: THREE.ZeroFactor,
    })
    m.onBeforeCompile = (sh) => { // fade towards white (= no change to the drawing) as the lift goes to 0
      sh.uniforms.uFade = fade
      sh.fragmentShader = 'uniform float uFade;\n' + sh.fragmentShader.replace('#include <dithering_fragment>',
        'gl_FragColor.rgb = mix(vec3(1.0), gl_FragColor.rgb, uFade);\n#include <dithering_fragment>')
    }
    return [kind, m]
  })) as Record<string, THREE.MeshStandardMaterial>, [parts, fade])
  useEffect(() => () => Object.values(mats).forEach((m) => m.dispose()), [mats])
  const group = useRef<THREE.Group>(null)
  useFrame(() => {
    const v = lift.current?.v ?? 0
    fade.value = THREE.MathUtils.smoothstep(v, 0, 1)
    if (group.current) group.current.visible = v > 0.002
  })
  return (
    <group ref={group} rotation-x={-Math.PI / 2} position-y={y} visible={false}>
      {parts.map(({ kind, geo }) => <mesh key={kind} geometry={geo} material={mats[kind]} renderOrder={1} raycast={() => null} />)}
    </group>
  )
}

export type { Pt }
