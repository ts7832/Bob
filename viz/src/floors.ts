// Room-aware floor materials for the 3D view. The material follows what the system read about each room (its printed
// name in any of the dataset's languages, or the detector's room type), and is generated in code (no downloaded assets).
// Floors are multiplied over the drawing, so every printed line and label stays visible on top. Display only.
import * as THREE from 'three'
import type { Loaded, Pt } from './types'

type FloorRoom = { name?: string | null; type?: string | null; polygon: Pt[] }
// Floor areas for the 3D floors, Photo mode and the clean view. CAD / BIM: the rooms as read. Scans: the rooms found
// between the image walls (whole rooms, up to the walls), each typed by the detector's room box that covers at least a
// quarter of it; rooms the detector did not type get a neutral stone floor ('corridor'). Display only.
export function floorRooms(l: Loaded): FloorRoom[] {
  const regs = l.context.regions
  if (!regs?.length) return l.context.rooms
  const box = (p: Pt[]) => { let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity; for (const [x, y] of p) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y) } return [x0, y0, x1, y1] }
  const typed = l.context.rooms.filter((r) => r.type).map((r) => ({ type: r.type!, b: box(r.polygon) }))
  const out: FloorRoom[] = []
  for (const rects of regs) {
    const rb = rects.map(box), area = rb.reduce((a, b) => a + (b[2] - b[0]) * (b[3] - b[1]), 0)
    let best: string | null = null, bestA = 0
    for (const t of typed) {
      let a = 0
      for (const b of rb) a += Math.max(0, Math.min(b[2], t.b[2]) - Math.max(b[0], t.b[0])) * Math.max(0, Math.min(b[3], t.b[3]) - Math.max(b[1], t.b[1]))
      if (a > bestA) { bestA = a; best = t.type }
    }
    const type = best && bestA >= 0.25 * area ? best : 'corridor'
    for (const r of rects) out.push({ name: null, type, polygon: r })
  }
  return out
}

export type FloorKind = 'wood' | 'tile' | 'stone' | 'concrete' | 'deck'

const RULES: [FloorKind, RegExp][] = [
  ['tile', /\b(bad|bagno|bain|salle d.?eau|douche|dusche|wc|toilet|toilette|bath|bathroom|wc_room|shower|pesu|kylpy|vanni|du[sš]|omkl|laundry|wasch|lavanderia|sauna)/i],
  ['deck', /\b(balkon|balcone|balcon|balcony|terrass|terrazz|terrace|loggia|parveke|terassi|veranda|porch)/i],
  ['concrete', /\b(reduit|ripostiglio|depot|abstell|keller|cantina|cave|storage|technik|tekni|kedel|affald|server|shaft|garage|utility|varasto|lift|ascensore|ascenseur|treppenhaus|scala|escalier|stair|cellier)/i],
  ['stone', /\b(k[üu]che|kueche|cucina|cuisine|kitchen|keitti|k[øo]kken|k[öo][öo]k|korridor|corridoio|couloir|corridor|gang|flur|entr[ée]e|ingresso|eingang|hall|entry|disimpegno|vestibul|eteinen|esik|b[üu]ro|office|ufficio|bureau)/i],
  ['wood', /\b(zimmer|stanza|chambre|camera|room|wohnen|soggiorno|s[ée]jour|living|schlaf|bedroom|v[æa]relse|stue|tuba|makuu|olohuone|salon|dining|essen|pranzo|dressing|bedroom)/i],
]

export function floorKind(name?: string | null, type?: string | null, fallback: FloorKind = 'wood'): FloorKind {
  const s = `${name ?? ''} ${type ?? ''}`.replace(/_/g, ' ')
  for (const [k, re] of RULES) if (re.test(s)) return k
  return fallback
}

// Real-world size (m) one texture tile covers, so planks and tiles come out at believable sizes.
export const TILE_M: Record<FloorKind, number> = { wood: 1.6, tile: 1.2, stone: 1.8, concrete: 3, deck: 1.2 }

const cache = new Map<FloorKind, THREE.Texture>()

export function floorTexture(kind: FloorKind): THREE.Texture {
  const hit = cache.get(kind)
  if (hit) return hit
  const N = 512, c = document.createElement('canvas')
  c.width = c.height = N
  const g = c.getContext('2d')!
  let seed = 7
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
  const noise = (alpha: number, size = 1) => {
    for (let i = 0; i < 9000; i++) {
      g.fillStyle = rnd() < 0.5 ? `rgba(0,0,0,${alpha * rnd()})` : `rgba(255,255,255,${alpha * rnd()})`
      g.fillRect(rnd() * N, rnd() * N, size, size)
    }
  }
  if (kind === 'wood' || kind === 'deck') {
    const rows = kind === 'wood' ? 8 : 6, h = N / rows
    const base = kind === 'wood' ? [196, 156, 112] : [150, 120, 92]
    for (let r = 0; r < rows; r++) {
      const off = rnd() * N
      for (const x0 of [off - N, off]) { // two staggered boards per row, wrapped so the texture tiles seamlessly
        const t = (rnd() - 0.5) * 26
        g.fillStyle = `rgb(${base[0] + t},${base[1] + t * 0.85},${base[2] + t * 0.7})`
        g.fillRect(x0, r * h, N, h)
        for (let k = 0; k < 14; k++) { // grain
          g.strokeStyle = `rgba(90,60,30,${0.05 + rnd() * 0.08})`
          g.lineWidth = 0.6 + rnd()
          const y = r * h + rnd() * h
          g.beginPath(); g.moveTo(x0, y)
          g.bezierCurveTo(x0 + N * 0.3, y + (rnd() - 0.5) * 6, x0 + N * 0.6, y + (rnd() - 0.5) * 6, x0 + N, y + (rnd() - 0.5) * 4)
          g.stroke()
        }
        g.fillStyle = 'rgba(60,40,20,0.45)'
        g.fillRect(x0 + N - 1.5, r * h, 1.5, h) // board end
      }
      g.fillStyle = 'rgba(60,40,20,0.5)'
      g.fillRect(0, r * h, N, kind === 'wood' ? 1.5 : 4) // seam (decking has gaps)
    }
  } else if (kind === 'tile' || kind === 'stone') {
    const n = kind === 'tile' ? 4 : 3, s = N / n
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
      const t = (rnd() - 0.5) * 10
      g.fillStyle = kind === 'tile' ? `rgb(${207 + t},${214 + t},${218 + t})` : `rgb(${214 + t},${204 + t},${188 + t})`
      g.fillRect(i * s, j * s, s, s)
    }
    noise(kind === 'tile' ? 0.05 : 0.09)
    g.fillStyle = kind === 'tile' ? 'rgb(172,182,188)' : 'rgb(170,160,146)'
    for (let i = 0; i <= n; i++) { g.fillRect(i * s - 1.5, 0, 3, N); g.fillRect(0, i * s - 1.5, N, 3) }
  } else {
    g.fillStyle = 'rgb(186,184,178)'
    g.fillRect(0, 0, N, N)
    noise(0.12, 2); noise(0.06, 5)
  }
  const tex = new THREE.CanvasTexture(c)
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping
  tex.colorSpace = THREE.SRGBColorSpace
  tex.anisotropy = 8
  cache.set(kind, tex)
  return tex
}

// One texture with every room's floor and the drawing multiplied on top (white paper -> floor, printed lines stay).
// Used by the path-traced photo mode, which needs plain opaque materials. Built once on a 2D canvas.
export function planComposite(
  source: HTMLImageElement | HTMLCanvasElement, width: number, height: number,
  rooms: { name?: string | null; type?: string | null; polygon: [number, number][] }[], pxPerM: number, fallback: FloorKind, size = 2048,
) {
  const k = Math.min(1, size / Math.max(width, height))
  const c = document.createElement('canvas')
  c.width = Math.round(width * k); c.height = Math.round(height * k)
  const g = c.getContext('2d')!
  g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height)
  for (const r of rooms) {
    if (r.polygon.length < 3) continue
    const kind = floorKind(r.name, r.type, fallback)
    const img = floorTexture(kind).image as HTMLCanvasElement
    const pat = g.createPattern(img, 'repeat')!
    pat.setTransform(new DOMMatrix().scale((TILE_M[kind] * pxPerM * k) / img.width))
    g.save()
    g.beginPath()
    r.polygon.forEach(([x, y], i) => (i ? g.lineTo(x * k, y * k) : g.moveTo(x * k, y * k)))
    g.closePath(); g.clip()
    g.fillStyle = pat; g.fillRect(0, 0, c.width, c.height)
    g.restore()
  }
  g.globalCompositeOperation = 'multiply'
  g.drawImage(source, 0, 0, c.width, c.height)
  return c
}
