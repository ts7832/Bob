// Clean view: the plan redrawn from what the system detected or read (walls, rooms with their names and areas, one
// symbol set), shown next to the original with a swipe divider. It is drawn from the same data the answers use, so a
// reviewer sees at a glance what the system "thinks" the plan is, and where that differs from the drawing.
// Display only. Labelled "redrawn from detections" in the view.
import type { Element, Loaded, Pt } from './types'
import { anchor, bbox, obb, obbRect, ringArea, shapesFromRings } from './geom'
import { GLYPH, normCls, type Cls } from './ink'
import { floorKind, floorRooms, type FloorKind } from './floors'

const PAPER = '#fcfcfd', WALL = '#1f2430', LABEL = '#2f3644', MUTED = '#6b7280', ROOM_LINE = '#c3cad4'
const ROOM_FILL: Record<FloorKind, string> = { wood: '#f3e9db', tile: '#d9ebf5', stone: '#ebe9e5', concrete: '#e3e6ea', deck: '#e0eed8' }
export const SYMBOL_COLOR: Partial<Record<Cls, string>> = {
  door: '#2563eb', window: '#0d9488', wc: '#9333ea', sink: '#db2777', bathtub: '#0891b2', shower: '#4f46e5', stairs: '#57534e',
  stove: '#b45309', kitchen: '#a16207', fixture: '#64748b', column: WALL, washer: '#0e7490', shaft: '#57534e', cabinet: '#8b5e34',
}
const ROOM_NAME: Record<string, string> = {
  bedroom: 'Bedroom', kitchen: 'Kitchen', bathroom: 'Bathroom', wc_room: 'WC', balcony: 'Balcony', sauna_room: 'Sauna',
  living_room: 'Living room', dining: 'Dining', entry: 'Entry', storage: 'Storage', utility: 'Utility', dressing_room: 'Dressing room',
  terrace: 'Terrace', garage: 'Garage', room: 'Room', other_room: 'Room',
}

// Is there anything to redraw? (judges' PDF sheets with no detections: no clean view, the button stays hidden)
export function canClean(l: Loaded) {
  if (l.context.rooms.length || l.plan.some((p) => p.type !== 'floor' && p.type !== 'apartment')) return true
  return l.drawing.kind !== 'image' && l.context.walls.length > 0 // scans find walls lazily: they always have plan items
}

// The symbol classes the clean view draws for this sheet, for its legend (same rules as buildClean below).
export function cleanClasses(l: Loaded): Cls[] {
  const out = new Set<Cls>()
  if (l.drawing.kind === 'svg') {
    for (const e of l.guids.values()) if (e.markup && !/Column/.test(e.type)) { const c = normCls(e.type); if (c) out.add(c === 'fixture' && /Sanitary/.test(e.type) ? 'wc' : c) }
  } else {
    for (const o of l.context.openings) out.add(o.cls)
    for (const f of l.context.fixtures) { const c = normCls(f.cls); if (c) out.add(c) }
    if (l.drawing.kind === 'image' && l.plan.some((p) => p.type === 'door')) out.add('door')
  }
  const order: Cls[] = ['door', 'window', 'wc', 'sink', 'bathtub', 'shower', 'washer', 'kitchen', 'cabinet', 'stairs', 'shaft', 'stove', 'fixture']
  return order.filter((c) => out.has(c))
}

export async function buildClean(l: Loaded): Promise<HTMLCanvasElement | null> {
  if (!canClean(l)) return null
  const d = l.drawing, L = Math.max(d.width, d.height)
  const S = Math.min(4096, Math.max(2048, L)), k = S / L // small scans are drawn bigger so text and lines stay crisp
  const c = document.createElement('canvas')
  c.width = Math.round(d.width * k); c.height = Math.round(d.height * k)
  const g = c.getContext('2d')!
  g.fillStyle = PAPER; g.fillRect(0, 0, c.width, c.height)
  g.setTransform(k, 0, 0, k, 0, 0) // from here on: sheet px
  const px = 1 / k // one canvas pixel, in sheet px
  const ring = (pts: Pt[]) => { pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath() }
  g.lineJoin = 'round'; g.lineCap = 'round'

  // 1. rooms: tinted by what kind of room they are (name in any language, or the detector's type); scans: the whole
  //    rooms between the walls, not just the detector's boxes
  const scan = !!l.context.regions?.length
  for (const r of floorRooms(l)) {
    if (r.polygon.length < 3) continue
    g.beginPath(); ring(r.polygon)
    g.fillStyle = ROOM_FILL[floorKind(r.name, r.type)]; g.fill()
    if (!scan) { g.strokeStyle = ROOM_LINE; g.lineWidth = 1.2 * px; g.stroke() }
  }

  // 2. walls (exact on CAD and BIM sheets, found in the image on scans)
  g.fillStyle = WALL
  for (const sh of shapesFromRings(d, l.context.walls, l.context.wallsDisjoint)) {
    g.beginPath()
    const back = (v: { x: number; y: number }): Pt => [v.x + d.width / 2, d.height / 2 - v.y] // shape space -> sheet px
    ring(sh.getPoints().map(back))
    for (const h of sh.holes) ring(h.getPoints().map(back))
    g.fill('evenodd')
  }

  // 3. one symbol set: openings cut into the walls, fixtures as outlined boxes with their glyph
  const sym = (cls: Cls, poly: Pt[]) => {
    const col = SYMBOL_COLOR[cls] ?? MUTED, o = obb(poly)
    if (o.len < 2 * px || o.dep < px) return
    if (cls === 'door' || cls === 'window') {
      g.beginPath(); ring(poly); g.fillStyle = PAPER; g.fill() // the opening in the wall
      g.strokeStyle = col; g.lineWidth = 1.6 * px
      if (cls === 'window') { // glazing: three lines along the wall
        for (const f of [0.92, 0.0001]) { g.beginPath(); ring(obbRect(o, f)); g.stroke() }
      } else {
        g.beginPath(); ring(poly); g.fillStyle = col + '2e'; g.fill(); g.stroke()
      }
      return
    }
    const rr = obbRect(o, 1, Math.min(o.len, o.dep) * 0.06)
    g.beginPath(); ring(rr); g.fillStyle = '#fff'; g.fill()
    g.strokeStyle = col; g.lineWidth = 1.5 * px; g.stroke()
    const path = GLYPH[cls]
    if (!path) return
    const s = (0.72 * Math.min(o.len, o.dep)) / 16
    g.save()
    g.translate(o.cx, o.cy); g.rotate(Math.atan2(o.uy, o.ux)); g.scale(s, s); g.translate(-8, -8)
    g.lineWidth = 1.4; g.strokeStyle = col
    g.stroke(new Path2D(path))
    g.restore()
  }
  if (d.kind !== 'svg') {
    for (const o of l.context.openings) sym(o.cls, o.polygon)
    for (const f of l.context.fixtures) { const c2 = normCls(f.cls); if (c2) sym(c2, f.polygon) }
    if (d.kind === 'image') for (const p of l.plan) if (p.type === 'door') sym('door', p.polygon) // scan doors (not in context)
  } else {
    await bimSymbols(l, c, g)
  }

  // 4. room labels: name, number, area as the backend has them (printed label or polygon geometry)
  for (const r of l.context.rooms) {
    const name = r.name || (r.type ? ROOM_NAME[r.type] ?? null : null)
    if (!name || r.polygon.length < 3) continue
    const b = bbox(r.polygon), [x, y] = anchor({ polygon: r.polygon } as Element)
    let f = Math.min(Math.max(Math.sqrt(Math.abs(ringArea(r.polygon))) * 0.1, 0.0045 * L), 0.016 * L)
    const font = (w: number, s: number) => `${w} ${s}px system-ui, -apple-system, "Segoe UI", sans-serif`
    g.font = font(600, f)
    const fit = (0.9 * b.w) / Math.max(g.measureText(name).width, 1)
    if (fit < 1) { f *= fit; if (f < 0.003 * L) continue; g.font = font(600, f) }
    const sub = [r.number, r.area != null ? `${r.area.toFixed(2)} m²` : null].filter(Boolean).join('  ·  ')
    g.textAlign = 'center'; g.textBaseline = 'middle'
    g.fillStyle = LABEL
    g.fillText(name, x, sub ? y - 0.55 * f : y)
    if (sub) { g.font = font(400, 0.78 * f); g.fillStyle = MUTED; g.fillText(sub, x, y + 0.6 * f) }
  }
  return c
}

// BIM plans: every symbol element's own SVG paths, coloured by class (exact, like the evidence glow)
async function bimSymbols(l: Loaded, c: HTMLCanvasElement, g: CanvasRenderingContext2D) {
  const els = [...l.guids.values()].filter((e) => e.markup && !/Column/.test(e.type))
  if (!els.length) return
  const d = l.drawing
  const [x0, y0] = d.fromPx([0, 0]), [x1, y1] = d.fromPx([d.width, d.height])
  const upp = (x1 - x0) / c.width // svg units per canvas px
  const rule = (sel: string, col: string) => `${sel},${sel} *{stroke:${col}!important;fill:none!important;stroke-width:${(1.5 * upp).toFixed(5)}px!important}`
  const css = [rule('g', SYMBOL_COLOR.fixture!), rule('.IfcDoor', SYMBOL_COLOR.door!), rule('.IfcWindow', SYMBOL_COLOR.window!),
    rule('.IfcStair', SYMBOL_COLOR.stairs!), rule('.IfcStairFlight', SYMBOL_COLOR.stairs!), rule('.IfcSanitaryTerminal', SYMBOL_COLOR.wc!)].join('')
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x0} ${y0} ${x1 - x0} ${y1 - y0}" width="${c.width}" height="${c.height}"><style>${css}</style>${els.map((e) => e.markup).join('')}</svg>`
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = url })
    g.save(); g.setTransform(1, 0, 0, 1, 0, 0)
    g.drawImage(img, 0, 0, c.width, c.height)
    g.restore()
  } catch { /* symbols are optional */ } finally {
    URL.revokeObjectURL(url)
  }
}
