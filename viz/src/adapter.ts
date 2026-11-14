// Demo JSON (render / plan / sheet / ask) -> the view's model in sheet pixels. The only place source frames are handled:
//   image  : render px = sheet px (scans, PDF pages)
//   vector : Swiss DXF in metres, y already down in render items and ask evidence; /api/sheet polygons are y-up (y_flip)
//   svg    : BIM plan in viewBox units; evidence is guids, outlines come from the svg paths
import type { AskJSON, Answer, Context, Drawing, Element, Loaded, PlanJSON, Pt, RenderJSON, SheetJSON, VectorItem } from './types'
import { wallsFromImage } from './walls'
import { thickness } from './geom'
import { normCls } from './ink'

const MAX_PX = 4096 // long side of rasterised vector/svg sheets; safe texture size on any laptop GPU

// Same layer styles as demo/index.html, so the sheet looks the same in both views (stroke widths in metres).
const STYLE: Record<string, { fill?: string; stroke?: string; w?: number; dash?: number[] }> = {
  WALL_FILL: { fill: '#3d4048' }, WALL: { fill: '#3d4048', stroke: '#2b2e35', w: 0.01 },
  ROOM_OUTLINE: { stroke: '#b9c3cf', w: 0.015 }, UNIT_OUTLINE: { stroke: '#d64545', w: 0.03, dash: [0.25, 0.12] },
  DOOR: { stroke: '#374151', w: 0.015 }, WINDOW: { stroke: '#1f8a5b', w: 0.02 },
  FIXTURE: { stroke: '#6b7280', w: 0.012 }, ANNOTATION: { stroke: '#999', w: 0.01 },
}

let keySeq = 0
// What becomes a low ceramic/furniture volume in 3D, per source (display only)
const NOT_FIXTURE = new Set(['stairs', 'shaft'])
const SCAN_FIXTURES = new Set(['wc', 'sink', 'bathtub', 'shower', 'sauna_stove', 'sauna_bench'])
const BIM_FIXTURES = new Set(['IfcSanitaryTerminal', 'IfcFurnishingElement', 'IfcFurniture'])
// IFC classes drawn as plan symbols: their own paths glow when they are evidence (ink.ts)
const SYMBOL_IFC = /Door|Window|Sanitary|Furnish|Furniture|Stair|Column|FlowTerminal/
// IFC classes that stand up as walls in the 3D view (IFC2x3 exports mostly use IfcWallStandardCase)
const WALL_CLASSES = new Set(['IfcWall', 'IfcWallStandardCase', 'IfcWallElementedCase', 'IfcCurtainWall', 'IfcColumn'])

export async function prepare(render: RenderJSON, plan: PlanJSON | null, sheet: SheetJSON | null, imageUrl?: string): Promise<Loaded> {
  const key = `d${++keySeq}`
  let drawing: Drawing
  let guids = new Map<string, Element>()
  const walls: Pt[][] = []
  let lazyWalls: (() => Pt[][]) | null = null

  if (render.type === 'image') {
    const img = await loadSheetImage(imageUrl ?? render.url!)
    const width = render.width ?? (img instanceof HTMLImageElement ? img.naturalWidth : img.width)
    const height = render.height ?? (img instanceof HTMLImageElement ? img.naturalHeight : img.height)
    // PDF pages with /Rotate: the page image is turned, but the backend's positions may be in the unturned page frame
    const fix = frameFix(sheet, width, height, img)
    drawing = {
      key, kind: 'image', width, height, source: img, toPx: fix?.toPx ?? ((p) => p), fromPx: fix?.fromPx ?? ((p) => p), units: 'px',
      mPerPx: sheet?.scale?.m_per_px ?? null, note: render.note, frameNote: fix?.note ?? null, frameBad: fix?.bad ?? false,
    }
    for (const w of sheet?.walls ?? []) if (w.polygon?.length) walls.push(w.polygon.map(drawing.toPx))
    // scans without the segmentation model: display-only walls from the image (not for PDF pages: tables, text).
    // Found lazily, on the first switch to 3D, so opening a sheet in 2D stays fast.
    if (!walls.length && render.source_type === 'raster') {
      lazyWalls = () => { try { return wallsFromImage(img, width, height) } catch { return [] } }
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
  const openings: Context['openings'] = [], fixtures: Context['fixtures'] = []
  if (drawing.kind === 'vector') {
    for (const o of sheet?.openings ?? []) if (o.cls === 'door' && o.polygon?.length) openings.push({ cls: 'door', polygon: o.polygon.map((p) => drawing.toPx(flip(p))) })
    for (const it of render.items ?? []) if (it.k === 'poly' && it.layer === 'WINDOW' && it.closed && it.pts.length >= 4) openings.push({ cls: 'window', polygon: it.pts.map(drawing.toPx) })
    for (const f of sheet?.fixtures ?? []) if (f.bbox && !NOT_FIXTURE.has(f.cls)) fixtures.push({ cls: f.cls, polygon: boxPts(f.bbox, (p) => drawing.toPx(flip(p))) })
  } else if (drawing.kind === 'svg') {
    for (const e of guids.values()) {
      if (e.type === 'IfcWindow') openings.push({ cls: 'window', polygon: e.polygon })
      else if (BIM_FIXTURES.has(e.type)) fixtures.push({ cls: e.type, polygon: e.polygon })
    }
  } else {
    for (const it of plan?.items ?? []) {
      if (!it.bbox) continue
      if (it.type === 'window') openings.push({ cls: 'window', polygon: boxPts(it.bbox, drawing.toPx) })
      else if (it.type === 'door') openings.push({ cls: 'door', polygon: boxPts(it.bbox, drawing.toPx) }) // 3D: lintel + leaf
      else if (SCAN_FIXTURES.has(it.type)) fixtures.push({ cls: it.type, polygon: boxPts(it.bbox, drawing.toPx) })
    }
  }
  const rooms: Context['rooms'] = []
  for (const r of sheet?.rooms ?? []) {
    const poly = r.polygon?.length ? r.polygon.map((p) => drawing.toPx(flip(p))) : r.bbox ? boxPts(r.bbox.map(Number), (p) => drawing.toPx(flip(p))) : null
    if (poly) rooms.push({ id: r.id, name: r.name ?? null, type: r.cls ?? null, polygon: poly, number: r.number ?? null, area: r.area_m2 ?? null, areaSource: r.area_source ?? null })
  }
  if (drawing.kind === 'svg') for (const e of guids.values()) if (e.type === 'IfcSpace') rooms.push({ id: e.id, name: e.name, type: 'IfcSpace', polygon: e.polygon })

  const planEls: Element[] = (plan?.items ?? []).filter((it) => it.pts?.length || it.bbox?.length === 4).map((it, i) => ({
    id: `plan${i}`, type: it.type, how: it.how ?? null, confidence: it.conf ?? null,
    name: it.label ?? null, value: it.area_m2 != null ? `${it.area_m2} m²` : null,
    ...(it.pts?.length ? { geometry: 'polygon' as const, polygon: it.pts.map(drawing.toPx) }
      : { geometry: 'box' as const, polygon: boxPts(it.bbox!.map(Number), drawing.toPx) }),
  }))

  // evidence ids are the sheet's own ids: their class names the symbol (fx75 -> wc)
  const classes = new Map<string, string>()
  for (const r of sheet?.rooms ?? []) classes.set(r.id, 'room')
  for (const o of sheet?.openings ?? []) if (o.cls) classes.set(o.id, o.cls)
  for (const f of sheet?.fixtures ?? []) if (f.cls) classes.set(f.id, f.cls)

  let cached: Pt[][] | null = null
  const context: Context = {
    get walls() { return lazyWalls ? (cached ??= lazyWalls()) : walls },
    wallsDisjoint: !!lazyWalls, rooms, openings, fixtures,
  }
  return { drawing, plan: planEls, context, guids, classes }
}

// The backend reads PDF text and linework in the unrotated page frame; the page image is rendered with the page's /Rotate.
// On a turned page the sheet's texts reach past the image (e.g. y up to 6231 on a 6300 x 4500 image). Then try both
// quarter turns and keep the one that puts the texts on ink in the image; if neither clearly does, positions are unusable.
function frameFix(sheet: SheetJSON | null, W: number, H: number, img: HTMLImageElement | HTMLCanvasElement):
  { toPx: (p: Pt) => Pt; fromPx: (p: Pt) => Pt; note: string | null; bad: boolean } | null {
  const ts = (sheet?.texts ?? []).filter((t) => t.bbox?.length === 4)
  if (ts.length < 5) return null
  let mx = 0, my = 0
  for (const t of ts) { mx = Math.max(mx, t.bbox[2]); my = Math.max(my, t.bbox[3]) }
  if (mx <= W * 1.01 && my <= H * 1.01) return null // same frame as the image
  const bad = { toPx: (p: Pt) => p, fromPx: (p: Pt) => p, note: null, bad: true }
  if (!(mx <= H * 1.01 && my <= W * 1.01)) return bad
  const turns = [
    { deg: 90, toPx: (p: Pt): Pt => [W - p[1], p[0]], fromPx: (p: Pt): Pt => [p[1], W - p[0]] }, // shown turned clockwise
    { deg: 270, toPx: (p: Pt): Pt => [p[1], H - p[0]], fromPx: (p: Pt): Pt => [H - p[1], p[0]] },
  ]
  // ink test on a small copy of the page
  const iw = img instanceof HTMLImageElement ? img.naturalWidth : img.width, ih = img instanceof HTMLImageElement ? img.naturalHeight : img.height
  const s = Math.min(1, 1600 / Math.max(iw, ih)), w = Math.max(1, Math.round(iw * s)), h = Math.max(1, Math.round(ih * s))
  const c = document.createElement('canvas')
  c.width = w; c.height = h
  const g = c.getContext('2d', { willReadFrequently: true })!
  g.fillStyle = '#fff'; g.fillRect(0, 0, w, h); g.drawImage(img, 0, 0, w, h)
  let px: Uint8ClampedArray
  try { px = g.getImageData(0, 0, w, h).data } catch { return bad }
  const k = w / W // small-copy px per sheet px
  const big = [...ts].sort((a, b) => (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]) - (a.bbox[2] - a.bbox[0]) * (a.bbox[3] - a.bbox[1])).slice(0, 80)
  const score = (f: (p: Pt) => Pt) => {
    let hits = 0
    for (const t of big) {
      const [a, b] = [f([t.bbox[0], t.bbox[1]]), f([t.bbox[2], t.bbox[3]])]
      const x0 = Math.max(0, Math.floor(Math.min(a[0], b[0]) * k)), x1 = Math.min(w - 1, Math.ceil(Math.max(a[0], b[0]) * k))
      const y0 = Math.max(0, Math.floor(Math.min(a[1], b[1]) * k)), y1 = Math.min(h - 1, Math.ceil(Math.max(a[1], b[1]) * k))
      let n = 0, dark = 0
      for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
        const i = 4 * (y * w + x); n++
        if (0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2] < 150) dark++
      }
      if (n && dark / n > 0.04) hits++
    }
    return hits
  }
  const [a, b] = turns.map((t) => score(t.toPx))
  const best = a >= b ? turns[0] : turns[1], hi = Math.max(a, b), lo = Math.min(a, b)
  if (hi < 5 || hi < 1.5 * lo) return bad
  return { toPx: best.toPx, fromPx: best.fromPx, note: `The PDF page is turned ${best.deg}°: the backend's positions were turned to match it.`, bad: false }
}

const UNITS: Record<string, string> = { m2: 'm²', m3: 'm³', m: 'm', ft2: 'ft²', sqft: 'ft²', sf: 'ft²', ft3: 'ft³', ft: 'ft', lf: 'ft', count: '' }

// Category colours, first = the usual answer orange; all readable over a white sheet and the dark UI.
export const GROUP_COLORS = ['#ff7a00', '#2f6fde', '#12a37a', '#a24bd6', '#d63a7a', '#0d93b5', '#c99200', '#5f6f86']
const LENGTH = new Set(['m', 'ft', 'mm', 'cm', 'lf'])
const AREA = new Set(['m2', 'ft2', 'sqft', 'sf'])

export function toAnswer(ask: AskJSON, loaded: Loaded, question?: string | null): Answer {
  const tier = ask.honesty?.tier ?? null
  const ev = ask.evidence ?? []
  const isCount = ask.unit === 'count'
  const measure: Answer['measure'] = isCount ? 'count' : LENGTH.has(ask.unit ?? '') ? 'length' : AREA.has(ask.unit ?? '') ? 'area' : 'other'
  const cannot = !!ask.error || ask.answer == null || tier === 'cannot determine'
  const { toPx, mPerPx } = loaded.drawing
  const fmt = (v: number | string | null | undefined, u?: string | null) => v == null ? null : `${v}${u ? ' ' + (UNITS[u] ?? u) : ''}`

  // groups in the order the backend lists them (breakdown first, then evidence order)
  const order: string[] = []
  for (const b of ask.breakdown ?? []) if (b.group && !order.includes(b.group)) order.push(b.group)
  for (const e of ev) if (e.group && !order.includes(e.group)) order.push(e.group)
  const colorOf = (g?: string | null) => g ? GROUP_COLORS[order.indexOf(g) % GROUP_COLORS.length] : GROUP_COLORS[0]

  // class of an evidence item: the sheet's record for that id, else the id's own prefix (door21, win57, wc34)
  const clsOf = (id?: string) => (id ? normCls(loaded.classes.get(id) ?? id.replace(/[\d#_-]+$/, '')) : null)
  const elements: Element[] = []
  const seen = new Map<string, number>()
  const uid = (id: string) => { const n = seen.get(id) ?? 0; seen.set(id, n + 1); return n ? `${id}#${n + 1}` : id }
  ev.forEach((e, i) => {
    // count answers number their evidence on the drawing; the backend's own label wins when it is short
    const label = isCount && ev.length <= 150 ? (e.label && e.label.length < 4 ? e.label : String(i + 1)) : null
    const extra = {
      group: e.group ?? null, color: colorOf(e.group), valueText: fmt(e.value, e.unit),
      thicknessPx: e.thickness_m != null && mPerPx ? e.thickness_m / mPerPx : null,
      diameterPx: e.diameter_m != null && mPerPx ? e.diameter_m / mPerPx : null,
    }
    const base = { id: uid(e.id ?? `ev${i}`), label, how: e.shape, ...extra, cls: clsOf(e.id) }
    if (e.shape === 'polygon' && e.pts?.length) {
      elements.push({ ...base, type: 'evidence', geometry: 'polygon', polygon: e.pts.map(toPx) })
    } else if (e.shape === 'polyline' && e.pts && e.pts.length >= 2) {
      elements.push({ ...base, type: 'evidence', geometry: 'polyline', polygon: e.pts.map(toPx) })
    } else if (e.shape === 'box' && e.bbox) {
      const polygon = boxPts(e.bbox, toPx)
      const m = planOf(loaded.plan, polygon)
      elements.push({ ...base, type: 'evidence', geometry: 'box', polygon, confidence: m?.confidence ?? null, cls: base.cls ?? normCls(m?.type) })
    } else if (e.shape === 'guid' && e.id) {
      const g = loaded.guids.get(e.id)
      if (g) elements.push({ ...g, id: base.id, label, how: 'guid', ...extra, cls: normCls(g.type) })
    }
  })
  const groups: Answer['groups'] = order.map((name) => {
    const b = ask.breakdown?.find((x) => x.group === name)
    return { name, color: colorOf(name), count: b?.count ?? elements.filter((el) => el.group === name).length, value: fmt(b?.value, b?.unit ?? ask.unit) }
  })
  // never draw a highlight in the wrong place: positions off this page (or in a page frame we could not match) are dropped
  const { width: W, height: H, frameBad } = loaded.drawing
  const before = elements.length
  const onPage = frameBad ? [] : elements.filter((e) => {
    const b = bboxOf(e.polygon), mx = 0.01 * W, my = 0.01 * H
    return b[2] >= -mx && b[3] >= -my && b[0] <= W + mx && b[1] <= H + my
  })
  const outside = before - onPage.length
  elements.splice(0, elements.length, ...onPage)
  // count answers: two items with the same outline may be one thing counted twice; the reviewer should see that
  const dupes: string[] = []
  if (isCount && elements.length <= 400) {
    const bs = elements.map((e) => bboxOf(e.polygon))
    for (let i = 0; i < bs.length; i++) for (let j = i + 1; j < bs.length; j++) {
      const a = bs[i], b = bs[j], tol = 0.02 * Math.max(a[2] - a[0], a[3] - a[1], 1e-9)
      if (a.every((v, k) => Math.abs(v - b[k]) <= tol)) dupes.push(`${elements[i].label ?? i + 1} & ${elements[j].label ?? j + 1}`)
    }
  }
  const unit = ask.unit ? UNITS[ask.unit] ?? ask.unit : ''
  return {
    question: question ?? null,
    status: cannot ? 'cannot_answer' : 'answered',
    value: cannot ? null : `${ask.answer}${unit ? ' ' + unit : ''}`,
    isCount, measure, groups, tier: cannot ? 'cannot determine' : tier,
    deterministic: ask.honesty?.deterministic ?? null, basis: ask.honesty?.basis ?? null,
    explanation: ask.error ?? ask.explanation ?? null, method: ask.method ?? null,
    styleWarning: !!ask.honesty?.style_warning,
    elements: cannot ? [] : elements, dupes: cannot ? [] : dupes, outside: cannot ? 0 : outside, cited: ev.length, citedIds: ev.map((e, i) => e.id ?? `ev${i}`),
  }
}

// ---------------------------------------------------------------- helpers

const boxPts = (b: number[], f: (p: Pt) => Pt): Pt[] => {
  const [x0, y0, x1, y1] = b
  return [f([x0, y0]), f([x1, y0]), f([x1, y1]), f([x0, y1])]
}

// Evidence boxes carry no confidence or class; the same box in /api/plan does.
function planOf(plan: Element[], poly: Pt[]) {
  return plan.find((p) => p.geometry === 'box' && p.polygon.every((q, i) => Math.abs(q[0] - poly[i][0]) < 0.6 && Math.abs(q[1] - poly[i][1]) < 0.6))
}

function loadImage(url: string) {
  return new Promise<HTMLImageElement>((res, rej) => {
    const im = new Image()
    im.onload = () => res(im)
    im.onerror = () => rej(new Error(`could not load drawing image ${url}`))
    im.src = url
  })
}

// Very large sheets (judges' PNGs are 67-138 megapixels) can fail to decode at full size: decode a downscaled copy.
// The coordinate frame stays the original pixels (render width/height); crops scale from this copy.
async function loadSheetImage(url: string): Promise<HTMLImageElement | HTMLCanvasElement> {
  try {
    const im = await loadImage(url)
    await im.decode()
    return im
  } catch (first) {
    const res = await fetch(url).catch(() => null)
    if (!res || !res.ok) throw first instanceof Error ? first : new Error(`could not load drawing image ${url}`) // a real load error, not size
    const blob = await res.blob()
    const probe = await createImageBitmap(blob, { resizeWidth: 64, resizeQuality: 'low' }) // aspect ratio only
    const aspect = probe.height / probe.width
    probe.close()
    const L = 8192 // long side of the copy; portrait sheets scale by height
    const [rw, rh] = aspect > 1 ? [Math.round(L / aspect), L] : [L, Math.round(L * aspect)]
    const bmp = await createImageBitmap(blob, { resizeWidth: rw, resizeHeight: rh, resizeQuality: 'high' })
    const c = document.createElement('canvas')
    c.width = bmp.width; c.height = bmp.height
    c.getContext('2d')!.drawImage(bmp, 0, 0)
    bmp.close()
    return c
  }
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
  if (!svg.getAttribute('viewBox')) { // no viewBox: its user space is width x height (units dropped); make it explicit
    const num = (v: string | null) => parseFloat(v ?? '') || 1000
    svg.setAttribute('viewBox', `0 0 ${num(svg.getAttribute('width'))} ${num(svg.getAttribute('height'))}`)
  }
  const [vx, vy, vw, vh] = svg.getAttribute('viewBox')!.split(/[\s,]+/).map(Number)
  const k = MAX_PX / Math.max(vw, vh)
  const width = Math.round(vw * k), height = Math.round(vh * k)
  const toPx = (p: Pt): Pt => [(p[0] - vx) * k, (p[1] - vy) * k]
  const fromPx = (p: Pt): Pt => [p[0] / k + vx, p[1] / k + vy]

  // Outline of every guid element straight from its path data (M/L/A only, no transforms in these files).
  const guids = new Map<string, Element>()
  const ser = new XMLSerializer()
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
    const type = n.getAttribute('class') ?? 'element'
    const best = rings.filter((r) => r.length >= 3).sort((a, b) => Math.abs(area(b)) - Math.abs(area(a)))[0]
    // doors: the largest ring is the triangle closing the swing arc; the box of all paths is the whole symbol
    const usePoly = !!best && Math.abs(area(best)) > 1e-6 && !/Door/.test(type)
    const symbol = SYMBOL_IFC.test(type)
    const el: Element = {
      id, type, name: n.getAttribute('data-name'),
      geometry: usePoly ? 'polygon' : 'box', polygon: (usePoly ? best : bboxRing(pts)).map(toPx), how: 'guid',
      markup: symbol ? ser.serializeToString(n) : null, ext: symbol ? bboxRing(pts).map(toPx) : null,
    }
    const prev = guids.get(id) // one guid can appear in several groups (body + annotation): keep the biggest outline
    if (prev && symbol) { // ...but draw every group's paths, over the extent of all of them
      el.markup = (prev.markup ?? '') + el.markup
      el.ext = bboxRing([...(prev.ext ?? []), ...(el.ext ?? [])])
    }
    if (!prev || Math.abs(area(el.polygon)) > Math.abs(area(prev.polygon))) guids.set(id, el)
    else if (symbol) { prev.markup = el.markup; prev.ext = el.ext }
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

// SVG path data -> rings of points (end points of every segment; curves are not sampled, fine for outlines).
// Handles absolute and relative M L H V C S Q T A Z.
const ARGS: Record<string, number> = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0 }
function pathRings(d: string): Pt[][] {
  const rings: Pt[][] = []
  let cur: Pt[] = [], x = 0, y = 0, sx = 0, sy = 0
  const re = /([MLHVCSQTAZmlhvcsqtaz])([^MLHVCSQTAZmlhvcsqtaz]*)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(d))) {
    const c = m[1], C = c.toUpperCase(), rel = c !== C, n = ARGS[C]
    const nums = (m[2].match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi) ?? []).map(Number)
    if (C === 'Z') { if (cur.length) rings.push(cur); cur = []; x = sx; y = sy; continue }
    for (let i = 0; i + n <= nums.length; i += n) {
      const a = nums.slice(i, i + n)
      const px = x, py = y
      if (C === 'H') x = rel ? x + a[0] : a[0]
      else if (C === 'V') y = rel ? y + a[0] : a[0]
      else { const ex = a[n - 2], ey = a[n - 1]; x = rel ? x + ex : ex; y = rel ? y + ey : ey }
      if (C === 'A' && cur.length) cur.push(...arcPts(px, py, x, y, Math.max(a[0], a[1]), a[3], a[4])) // door swings bulge
      if (C === 'M' && i === 0) { if (cur.length) rings.push(cur); cur = []; sx = x; sy = y }
      cur.push([x, y]) // further pairs after M are line-tos, as in SVG
    }
  }
  if (cur.length) rings.push(cur)
  return rings
}

// Points inside a circular SVG arc (end points excluded), so a rotated door swing's outline covers its bulge.
function arcPts(x0: number, y0: number, x1: number, y1: number, r: number, large: number, sweep: number): Pt[] {
  const dx = x1 - x0, dy = y1 - y0, c = Math.hypot(dx, dy) / 2
  if (!(c > 0) || !(r > 0)) return []
  const R = Math.max(r, c), d = Math.sqrt(Math.max(R * R - c * c, 0)), sg = large === sweep ? -1 : 1
  const cx = (x0 + x1) / 2 - (sg * d * dy) / (2 * c), cy = (y0 + y1) / 2 + (sg * d * dx) / (2 * c)
  const t0 = Math.atan2(y0 - cy, x0 - cx)
  let dt = Math.atan2(y1 - cy, x1 - cx) - t0
  if (sweep && dt < 0) dt += 2 * Math.PI
  if (!sweep && dt > 0) dt -= 2 * Math.PI
  const out: Pt[] = []
  for (let k = 1; k < 8; k++) out.push([cx + R * Math.cos(t0 + (dt * k) / 8), cy + R * Math.sin(t0 + (dt * k) / 8)])
  return out
}

export function area(p: Pt[]) {
  let a = 0
  for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length]; a += x1 * y2 - x2 * y1 }
  return a / 2
}

const bboxOf = (pts: Pt[]) => {
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
}

const bboxRing = (pts: Pt[]): Pt[] => {
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
  const x0 = Math.min(...xs), y0 = Math.min(...ys), x1 = Math.max(...xs), y1 = Math.max(...ys)
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
}
