// Demo JSON (render / plan / sheet / ask) -> the view's model in sheet pixels. The only place source frames are handled:
//   image  : render px = sheet px (scans, PDF pages)
//   vector : Swiss DXF in metres, y already down in render items and ask evidence; /api/sheet polygons are y-up (y_flip)
//   svg    : BIM plan in viewBox units; evidence is guids, outlines come from the svg paths
import type { AskJSON, Answer, Context, Drawing, Element, Loaded, PlanJSON, Pt, RenderJSON, SheetJSON, VectorItem } from './types'
import { wallsFromImage } from './walls'
import { thickness } from './geom'

const MAX_PX = 4096 // long side of rasterised vector/svg sheets; safe texture size on any laptop GPU

// Same layer styles as demo/index.html, so the sheet looks the same in both views (stroke widths in metres).
const STYLE: Record<string, { fill?: string; stroke?: string; w?: number; dash?: number[] }> = {
  WALL_FILL: { fill: '#3d4048' }, WALL: { fill: '#3d4048', stroke: '#2b2e35', w: 0.01 },
  ROOM_OUTLINE: { stroke: '#b9c3cf', w: 0.015 }, UNIT_OUTLINE: { stroke: '#d64545', w: 0.03, dash: [0.25, 0.12] },
  DOOR: { stroke: '#374151', w: 0.015 }, WINDOW: { stroke: '#1f8a5b', w: 0.02 },
  FIXTURE: { stroke: '#6b7280', w: 0.012 }, ANNOTATION: { stroke: '#999', w: 0.01 },
}

let keySeq = 0
// IFC classes that stand up as walls in the 3D view (IFC2x3 exports mostly use IfcWallStandardCase)
const WALL_CLASSES = new Set(['IfcWall', 'IfcWallStandardCase', 'IfcWallElementedCase', 'IfcCurtainWall', 'IfcColumn'])

export async function prepare(render: RenderJSON, plan: PlanJSON | null, sheet: SheetJSON | null, imageUrl?: string): Promise<Loaded> {
  const key = `d${++keySeq}`
  let drawing: Drawing
  let guids = new Map<string, Element>()
  const walls: Pt[][] = []

  if (render.type === 'image') {
    const img = await loadImage(imageUrl ?? render.url!)
    const width = render.width ?? img.naturalWidth, height = render.height ?? img.naturalHeight
    drawing = { key, kind: 'image', width, height, source: img, toPx: (p) => p, fromPx: (p) => p, units: 'px', mPerPx: sheet?.scale?.m_per_px ?? null, note: render.note }
    for (const w of sheet?.walls ?? []) if (w.polygon?.length) walls.push(w.polygon)
    // scans without the segmentation model: display-only walls from the image (not for PDF pages: tables, text)
    if (!walls.length && render.source_type === 'raster') {
      try { walls.push(...wallsFromImage(img, width, height)) } catch { /* 3D just has no walls */ }
    }
  } else if (render.type === 'vector') {
    const v = rasterVector(render)
    drawing = { key, kind: 'vector', ...v, units: 'm', note: render.note }
    for (const it of render.items ?? [])
      if (it.k === 'poly' && (it.layer === 'WALL' || it.layer === 'WALL_FILL') && it.pts.length >= 3) walls.push(it.pts.map(drawing.toPx))
  } else if (render.type === 'svg') {
    const s = await rasterSvg(render.svg!)
    drawing = { key, kind: 'svg', width: s.width, height: s.height, source: s.canvas, toPx: s.toPx, fromPx: s.fromPx, units: 'svg', mPerPx: null, note: render.note }
    guids = s.guids
    const ws = [...guids.values()].filter((e) => WALL_CLASSES.has(e.type)).map((e) => e.polygon)
    // drop shapes far thicker than a typical wall (arcs reduced to end points can make big triangles)
    const ts = ws.map(thickness), med = [...ts].sort((x, y) => x - y)[ts.length >> 1] ?? 0
    ws.forEach((w, i) => { if (!med || ts[i] <= 4 * med) walls.push(w) })
  } else {
    throw new Error(render.note || 'No drawing to show for this file.')
  }

  // Sheet geometry in the sheet's own frame: metres with y up on vector sheets, so flip y back into the render frame.
  const flip = sheet?.y_flip ? (p: Pt): Pt => [p[0], -p[1]] : (p: Pt) => p
  const rooms: Context['rooms'] = []
  for (const r of sheet?.rooms ?? []) {
    const poly = r.polygon?.length ? r.polygon.map((p) => drawing.toPx(flip(p))) : r.bbox ? boxPts(r.bbox.map(Number), (p) => drawing.toPx(flip(p))) : null
    if (poly) rooms.push({ id: r.id, name: r.name ?? null, polygon: poly })
  }
  if (drawing.kind === 'svg') for (const e of guids.values()) if (e.type === 'IfcSpace') rooms.push({ id: e.id, name: e.name, polygon: e.polygon })

  const planEls: Element[] = (plan?.items ?? []).map((it, i) => ({
    id: `plan${i}`, type: it.type, how: it.how ?? null, confidence: it.conf ?? null,
    name: it.label ?? null, value: it.area_m2 != null ? `${it.area_m2} m²` : null,
    ...(it.pts?.length ? { geometry: 'polygon' as const, polygon: it.pts.map(drawing.toPx) }
      : { geometry: 'box' as const, polygon: boxPts(it.bbox!.map(Number), drawing.toPx) }),
  }))

  return { drawing, plan: planEls, context: { walls, rooms }, guids }
}

const UNITS: Record<string, string> = { m2: 'm²', m3: 'm³', m: 'm', ft2: 'ft²', ft3: 'ft³', ft: 'ft', count: '' }

export function toAnswer(ask: AskJSON, loaded: Loaded, question?: string | null): Answer {
  const tier = ask.honesty?.tier ?? null
  const ev = ask.evidence ?? []
  const isCount = ask.unit === 'count'
  const cannot = !!ask.error || ask.answer == null || tier === 'cannot determine'
  const { toPx } = loaded.drawing
  const elements: Element[] = []
  ev.forEach((e, i) => {
    // count answers number their evidence on the drawing; the backend's own label wins when it is short
    const label = isCount && ev.length <= 150 ? (e.label && e.label.length < 4 ? e.label : String(i + 1)) : null
    const base = { id: e.id ?? `ev${i}`, label, how: e.shape }
    if (e.shape === 'polygon' && e.pts?.length) {
      elements.push({ ...base, type: 'evidence', geometry: 'polygon', polygon: e.pts.map(toPx) })
    } else if (e.shape === 'box' && e.bbox) {
      const polygon = boxPts(e.bbox, toPx)
      elements.push({ ...base, type: 'evidence', geometry: 'box', polygon, confidence: confOf(loaded.plan, polygon) })
    } else if (e.shape === 'guid' && e.id) {
      const g = loaded.guids.get(e.id)
      if (g) elements.push({ ...g, label, how: 'guid' })
    }
  })
  const unit = ask.unit ? UNITS[ask.unit] ?? ask.unit : ''
  return {
    question: question ?? null,
    status: cannot ? 'cannot_answer' : 'answered',
    value: cannot ? null : `${ask.answer}${unit ? ' ' + unit : ''}`,
    isCount, tier: cannot ? 'cannot determine' : tier,
    deterministic: ask.honesty?.deterministic ?? null, basis: ask.honesty?.basis ?? null,
    explanation: ask.error ?? ask.explanation ?? null, method: ask.method ?? null,
    styleWarning: !!ask.honesty?.style_warning,
    elements: cannot ? [] : elements, cited: ev.length,
  }
}

// ---------------------------------------------------------------- helpers

const boxPts = (b: number[], f: (p: Pt) => Pt): Pt[] => {
  const [x0, y0, x1, y1] = b
  return [f([x0, y0]), f([x1, y0]), f([x1, y1]), f([x0, y1])]
}

// Evidence boxes carry no confidence; the same box in /api/plan does.
function confOf(plan: Element[], poly: Pt[]) {
  const m = plan.find((p) => p.geometry === 'box' && p.polygon.every((q, i) => Math.abs(q[0] - poly[i][0]) < 0.6 && Math.abs(q[1] - poly[i][1]) < 0.6))
  return m?.confidence ?? null
}

function loadImage(url: string) {
  return new Promise<HTMLImageElement>((res, rej) => {
    const im = new Image()
    im.onload = () => res(im)
    im.onerror = () => rej(new Error(`could not load drawing image ${url}`))
    im.src = url
  })
}

function rasterVector(r: RenderJSON) {
  const [x0, y0, x1, y1] = r.bounds!
  const pad = (x1 - x0) * 0.04
  const W = x1 - x0 + 2 * pad, H = y1 - y0 + 2 * pad
  const k = MAX_PX / Math.max(W, H) // sheet px per metre
  const c = document.createElement('canvas')
  c.width = Math.round(W * k); c.height = Math.round(H * k)
  const g = c.getContext('2d')!
  g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height)
  const toPx = (p: Pt): Pt => [(p[0] - x0 + pad) * k, (p[1] - y0 + pad) * k]
  g.lineJoin = 'round'; g.lineCap = 'round'
  const texts: Extract<VectorItem, { k: 'text' }>[] = []
  for (const it of r.items ?? []) {
    if (it.k === 'text') { texts.push(it); continue }
    const st = STYLE[it.layer] ?? { stroke: '#bbb', w: 0.01 }
    g.beginPath()
    if (it.k === 'poly') {
      it.pts.forEach((p, i) => { const [x, y] = toPx(p); i ? g.lineTo(x, y) : g.moveTo(x, y) })
      if (it.closed || it.layer === 'WALL_FILL') g.closePath()
    } else if (it.k === 'circle') {
      const [x, y] = toPx(it.c); g.arc(x, y, it.r * k, 0, Math.PI * 2)
    } else if (it.k === 'arc') { // DXF angles, counter-clockwise; y is already down (same as demo/index.html)
      const [x, y] = toPx(it.c); const rad = Math.PI / 180
      g.arc(x, y, it.r * k, -it.a0 * rad, -it.a1 * rad, true)
    }
    if (st.fill && (it.k !== 'poly' || it.closed || it.layer === 'WALL_FILL')) { g.fillStyle = st.fill; g.fill() }
    if (st.stroke) {
      g.strokeStyle = st.stroke; g.lineWidth = Math.max(1, (st.w ?? 0.01) * k)
      g.setLineDash(st.dash ? st.dash.map((d) => d * k) : [])
      g.stroke()
    }
  }
  g.setLineDash([]); g.fillStyle = '#374151'; g.textAlign = 'center'
  for (const t of texts) { // drawn last and in pixels: tiny font sizes under a transform render badly
    const [x, y] = toPx(t.p)
    g.font = `${Math.max(6, t.h * 1.1 * k)}px system-ui, -apple-system, sans-serif`
    g.fillText(t.t, x, y)
  }
  const fromPx = (p: Pt): Pt => [p[0] / k + x0 - pad, p[1] / k + y0 - pad]
  return { width: c.width, height: c.height, source: c, toPx, fromPx, mPerPx: 1 / k }
}

async function rasterSvg(text: string) {
  const doc = new DOMParser().parseFromString(text, 'image/svg+xml')
  const svg = doc.documentElement
  const [vx, vy, vw, vh] = (svg.getAttribute('viewBox') ?? '0 0 1000 1000').split(/[\s,]+/).map(Number)
  const k = MAX_PX / Math.max(vw, vh)
  const width = Math.round(vw * k), height = Math.round(vh * k)
  const toPx = (p: Pt): Pt => [(p[0] - vx) * k, (p[1] - vy) * k]
  const fromPx = (p: Pt): Pt => [p[0] / k + vx, p[1] / k + vy]

  // Outline of every guid element straight from its path data (M/L/A only, no transforms in these files).
  const guids = new Map<string, Element>()
  doc.querySelectorAll('[data-guid]').forEach((n) => {
    const id = n.getAttribute('data-guid')!
    const rings: Pt[][] = []
    n.querySelectorAll('path').forEach((p) => rings.push(...pathRings(p.getAttribute('d') ?? '')))
    n.querySelectorAll('line').forEach((l) => {
      const v = ['x1', 'y1', 'x2', 'y2'].map((a) => Number(l.getAttribute(a)))
      rings.push([[v[0], v[1]], [v[2], v[3]]])
    })
    const pts = rings.flat()
    if (!pts.length) return
    const best = rings.filter((r) => r.length >= 3).sort((a, b) => Math.abs(area(b)) - Math.abs(area(a)))[0]
    const usePoly = !!best && Math.abs(area(best)) > 1e-6
    const el: Element = {
      id, type: n.getAttribute('class') ?? 'element', name: n.getAttribute('data-name'),
      geometry: usePoly ? 'polygon' : 'box', polygon: (usePoly ? best : bboxRing(pts)).map(toPx), how: 'guid',
    }
    const prev = guids.get(id) // one guid can appear in several groups (body + annotation): keep the biggest outline
    if (!prev || Math.abs(area(el.polygon)) > Math.abs(area(prev.polygon))) guids.set(id, el)
  })

  svg.setAttribute('width', String(width)); svg.setAttribute('height', String(height))
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' }))
  try {
    const img = await loadImage(url)
    const c = document.createElement('canvas')
    c.width = width; c.height = height
    const g = c.getContext('2d')!
    g.fillStyle = '#fff'; g.fillRect(0, 0, width, height)
    g.drawImage(img, 0, 0, width, height)
    return { canvas: c, width, height, toPx, fromPx, guids }
  } finally {
    URL.revokeObjectURL(url)
  }
}

// "M x,y L x,y A rx,ry rot large sweep x,y ... Z" -> rings of points (arc end points only; fine for outlines).
function pathRings(d: string): Pt[][] {
  const rings: Pt[][] = []
  let cur: Pt[] = []
  const re = /([MLAZmlaz])([^MLAZmlaz]*)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(d))) {
    const nums = (m[2].match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi) ?? []).map(Number)
    const cmd = m[1].toUpperCase()
    if (cmd === 'M') { if (cur.length) rings.push(cur); cur = []; for (let i = 0; i + 1 < nums.length; i += 2) cur.push([nums[i], nums[i + 1]]) }
    else if (cmd === 'L') for (let i = 0; i + 1 < nums.length; i += 2) cur.push([nums[i], nums[i + 1]])
    else if (cmd === 'A') for (let i = 0; i + 6 < nums.length; i += 7) cur.push([nums[i + 5], nums[i + 6]])
    else if (cmd === 'Z') { if (cur.length) rings.push(cur); cur = [] }
  }
  if (cur.length) rings.push(cur)
  return rings
}

export function area(p: Pt[]) {
  let a = 0
  for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length]; a += x1 * y2 - x2 * y1 }
  return a / 2
}

const bboxRing = (pts: Pt[]): Pt[] => {
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
  const x0 = Math.min(...xs), y0 = Math.min(...ys), x1 = Math.max(...xs), y1 = Math.max(...ys)
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
}
