// Photo mode: a GPU path tracer (three-gpu-pathtracer) renders the 3D model with global illumination, soft shadows and
// real glass, refining while the camera rests. It uses its own scene built from the same geometry with plain physical
// materials: the ground is one composited texture (room floors x the drawing), answer items glow and light the rooms.
// Display only: nothing here feeds an answer, and the 2D view never uses it.
import { useEffect, useMemo, type RefObject } from 'react'
import * as THREE from 'three'
import { useFrame, useThree } from '@react-three/fiber'
import { WebGLPathTracer, GradientEquirectTexture } from 'three-gpu-pathtracer'
import type { Answer, Loaded } from './types'
import type { BuildingGeo } from './Model'
import { outline, shapeOf, tube } from './geom'
import { planComposite } from './floors'

const BG = '#1a1f2b', ACCENT = '#ff6a00'

function buildPhotoScene(loaded: Loaded, b: BuildingGeo, drawingTex: THREE.Texture, wallH: number, answer: Answer | null, baseY: number) {
  const d = loaded.drawing, max = Math.max(d.width, d.height)
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(BG)
  const env = new GradientEquirectTexture(256)
  env.topColor.set('#ffffff'); env.bottomColor.set('#4b5060'); env.exponent = 1.6
  env.update()
  scene.environment = env
  scene.environmentIntensity = 0.75
  const owned: { dispose(): void }[] = [env]

  // ground: room floors with the drawing multiplied over them
  const pxPerM = d.mPerPx ? 1 / d.mPerPx : wallH / 2.6
  const plan = new THREE.CanvasTexture(planComposite(d.source, d.width, d.height, loaded.context.rooms, pxPerM, d.kind === 'svg' ? 'stone' : 'wood'))
  plan.colorSpace = THREE.SRGBColorSpace
  const groundMat = new THREE.MeshStandardMaterial({ map: plan, roughness: 0.75 })
  const groundGeo = new THREE.PlaneGeometry(d.width, d.height)
  const ground = new THREE.Mesh(groundGeo, groundMat)
  ground.rotation.x = -Math.PI / 2
  scene.add(ground)
  owned.push(plan, groundMat, groundGeo)

  // building: same geometry and transform as the raster view, physical materials
  const group = new THREE.Group()
  group.rotation.x = -Math.PI / 2
  group.position.y = baseY
  group.scale.z = wallH
  const mats = {
    cap: new THREE.MeshStandardMaterial({ map: drawingTex, roughness: 0.9 }),
    wall: new THREE.MeshStandardMaterial({ color: '#efebe4', roughness: 0.92 }),
    glass: new THREE.MeshPhysicalMaterial({ color: '#dfeef5', roughness: 0.02, transmission: 0.92, ior: 1.5, thickness: 2, transparent: true }),
    ceramic: new THREE.MeshStandardMaterial({ color: '#f7f7f5', roughness: 0.25 }),
    glow: new THREE.MeshStandardMaterial({ color: ACCENT, emissive: ACCENT, emissiveIntensity: 1.1, transparent: true, opacity: 0.32, roughness: 0.6 }),
  }
  owned.push(...Object.values(mats))
  if (b.solid) { group.add(new THREE.Mesh(b.solid[0], mats.cap), new THREE.Mesh(b.solid[1], mats.wall)) }
  if (b.fixtures) group.add(new THREE.Mesh(b.fixtures, mats.ceramic))
  if (b.glass) group.add(new THREE.Mesh(b.glass, mats.glass))
  // the answer: low glowing volumes that light their rooms
  const lines: THREE.Mesh[] = []
  if (answer?.status === 'answered') for (const el of answer.elements) {
    if (el.geometry === 'polyline') { // open lines glow as tubes (pipes at their size), never as closed filled shapes
      const g = tube(d, outline(el), (el.diameterPx ?? max / 700) / 2)
      owned.push(g)
      const m = new THREE.Mesh(g, mats.glow)
      m.position.y = baseY + (el.diameterPx != null ? wallH * 0.55 : wallH * 0.05)
      lines.push(m)
      continue
    }
    const g = new THREE.ExtrudeGeometry(shapeOf(d, outline(el)), { depth: 0.12, bevelEnabled: false })
    owned.push(g)
    group.add(new THREE.Mesh(g, mats.glow))
  }
  scene.add(group, ...lines)

  const sun = new THREE.DirectionalLight('#fff6ea', 2.4)
  sun.position.set(-0.55 * max, 1.25 * max, -0.42 * max)
  scene.add(sun, sun.target)
  scene.updateMatrixWorld(true)
  return { scene, dispose: () => owned.forEach((o) => o.dispose()) }
}

export function Photo({ loaded, b, tex, wallH, answer, baseY, status }: {
  loaded: Loaded; b: BuildingGeo; tex: THREE.Texture; wallH: number; answer: Answer | null; baseY: number
  status: RefObject<HTMLSpanElement | null> // sample counter, written directly (no React render per frame)
}) {
  const gl = useThree((s) => s.gl), camera = useThree((s) => s.camera), controls = useThree((s) => s.controls) as unknown as THREE.EventDispatcher<{ update: object }> | null
  const photo = useMemo(() => buildPhotoScene(loaded, b, tex, wallH, answer, baseY), [loaded, b, tex, wallH, answer, baseY])
  useEffect(() => photo.dispose, [photo])
  const pt = useMemo(() => {
    const p = new WebGLPathTracer(gl)
    p.tiles.set(2, 2)
    p.bounces = 5
    p.filterGlossyFactor = 0.5
    // sharp while the camera moves: the photo scene is rasterised; path tracing starts after 300 ms at rest and fades
    // in once a few samples exist (low-res-while-moving made it blurry, every move restarting the samples)
    p.minSamples = 4
    p.renderDelay = 300
    p.fadeDuration = 500
    p.dynamicLowRes = false
    p.textureSize.set(2048, 2048)
    return p
  }, [gl])
  useEffect(() => () => pt.dispose(), [pt])
  useEffect(() => { pt.setScene(photo.scene, camera) }, [pt, photo, camera])
  useEffect(() => {
    if (!controls) return
    const f = () => pt.updateCamera()
    controls.addEventListener('update', f)
    return () => controls.removeEventListener('update', f)
  }, [controls, pt])
  useFrame(() => {
    pt.renderSample()
    if (status.current) status.current.textContent = `Path tracing · ${Math.floor(pt.samples)} samples`
  }, 1)
  return null
}
