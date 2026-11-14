// Dev harness: uses the public API exactly as demo/index.html would. Not part of the bundle.
//   replay (default): saved responses from fixtures/   ·   ?live=1: the real demo server through the Vite proxy
//   URL state: ?d=<drawing stem>&q=<question index>&sel=<element id>
import { mount } from './index'
import type { AskJSON } from './types'

type Fixture = { stem: string; path: string; title: string; source: string; render_type: string }
type Ask = { question: string; truth: unknown; probe: boolean; response: AskJSON & { dataset_answer?: unknown } }

const P = new URLSearchParams(location.search)
const live = P.has('live')
const $ = (id: string) => document.getElementById(id)!
const esc = (t: unknown) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
const getJSON = (u: string) => fetch(u).then((r) => r.json())

const view = mount($('viewer'))
// ?quality=high|low pins the 3D quality (headless test browsers are slow and would otherwise drop to low)
const q = P.get('quality')
if (q === 'high' || q === 'low') view.setQuality(q)
;(window as unknown as { view: typeof view }).view = view
$('mode').textContent = live ? 'live server' : 'replay'

let index: Fixture[] = []
let stem = ''
let asks: Ask[] = []
let qi = -1

function syncUrl(sel?: string | null) {
  const u = new URLSearchParams()
  if (live) u.set('live', '1')
  u.set('d', stem)
  if (qi >= 0) u.set('q', String(qi))
  if (sel) u.set('sel', sel)
  if (P.get('mode')) u.set('mode', P.get('mode')!)
  history.replaceState(null, '', '?' + u)
}

async function openStem(s: string) {
  stem = s; qi = -1
  const fx = index.find((f) => f.stem === s)!
  ;($('drawing') as HTMLSelectElement).value = s
  if (live) await view.open(fx.path)
  else {
    const [render, plan, sheet] = await Promise.all(['render', 'plan', 'sheet'].map((n) => getJSON(`/fixtures/${s}/${n}.json`)))
    const ext = render.url ? (render.url.match(/\.(png|jpe?g)$/i)?.[0] ?? '.png').toLowerCase() : ''
    await view.load({ render, plan, sheet, imageUrl: render.type === 'image' ? `/fixtures/${s}/image${ext}` : undefined })
  }
  asks = await getJSON(`/fixtures/${s}/ask.json`)
  $('qs').innerHTML = asks.map((a, i) => `<button data-i="${i}" class="${a.probe ? 'probe' : ''}">${esc(a.question)}</button>`).join('')
  $('answer').innerHTML = ''
  syncUrl()
}

async function showAsk(i: number) {
  qi = i
  const a = asks[i]
  document.querySelectorAll('.qs button').forEach((b) => b.classList.toggle('on', (b as HTMLElement).dataset.i === String(i)))
  const r: Ask['response'] = live ? await postAsk(a.question) : a.response
  view.showAnswer(r, a.question)
  renderAnswer(r, a.truth)
  syncUrl()
}

async function postAsk(question: string) {
  const fx = index.find((f) => f.stem === stem)!
  const r = await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: fx.path, question }) })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

function renderAnswer(r: Ask['response'], truth: unknown) {
  const ok = truth == null ? '' : String(r.answer) === String(truth) ? `<span class="ok">✓ dataset ${esc(truth)}</span>` : `<span class="bad">✗ dataset ${esc(truth)}</span>`
  $('answer').innerHTML = `<div class="ans"><b>${esc(r.answer ?? '—')}</b> ${esc(r.unit ?? '')} ${ok}
    <div class="muted">${esc(r.honesty?.tier)} · ${esc(r.method)} · ${(r.evidence ?? []).length} evidence</div>
    <p>${esc(r.explanation)}</p></div>`
}

view.onSelect((el) => {
  $('sel').textContent = el ? `selected ${el.id}` : ''
  syncUrl(el?.id)
})

$('drawing').addEventListener('change', (e) => openStem((e.target as HTMLSelectElement).value))
$('qs').addEventListener('click', (e) => {
  const b = (e.target as HTMLElement).closest('button')
  if (b) showAsk(Number(b.dataset.i))
})
$('ask').addEventListener('submit', async (e) => {
  e.preventDefault()
  const q = ($('q') as HTMLInputElement).value.trim()
  if (!q) return
  // typed questions always go to the real backend (the demo server, through the Vite proxy); saved answers are only
  // used for the listed questions in replay mode
  const i = live ? -1 : asks.findIndex((a) => a.question.toLowerCase() === q.toLowerCase())
  if (i >= 0) { showAsk(i); return }
  $('answer').innerHTML = '<div class="ans muted">Asking the backend…</div>'
  try {
    const r = await postAsk(q)
    view.showAnswer(r, q); renderAnswer(r, null)
  } catch {
    $('answer').innerHTML = '<div class="ans bad">The backend is not reachable. Start it with: cd Bob && DRAWQA_LLM=0 PORT=8765 .venv/bin/python demo/server.py</div>'
  }
})

// ?synthetic=areas|baseboards|slabs|pipes: test answers built from a Swiss sheet's real room polygons, to exercise the
// view's grouped / linear / slab / pipe drawing before the backend emits them. Harness only; values are synthetic.
async function synthetic(kind: string) {
  const sheet = await getJSON(`/fixtures/${stem}/sheet.json`)
  const flip = (p: [number, number]) => [p[0], -p[1]] as [number, number] // sheet y-up -> render frame
  const rooms = (sheet.rooms ?? []).filter((r: { polygon?: unknown[] }) => r.polygon?.length)
  const area = (p: number[][]) => Math.abs(p.reduce((a, q, i) => { const r = p[(i + 1) % p.length]; return a + q[0] * r[1] - r[0] * q[1] }, 0) / 2)
  const per = (p: number[][]) => p.reduce((a, q, i) => { const r = p[(i + 1) % p.length]; return a + Math.hypot(r[0] - q[0], r[1] - q[1]) }, 0)
  const cat = (n = '') => /bad|wc/i.test(n) ? 'Wet rooms' : /k[üu]che|korridor|eingang|flur/i.test(n) ? 'Kitchen & circulation'
    : /terrass|balkon/i.test(n) ? 'Outdoor' : /b[üu]ro/i.test(n) ? 'Office' : /lift|treppe|reduit/i.test(n) ? 'Service' : 'Living & bedrooms'
  const r2 = (v: number) => Math.round(v * 100) / 100
  let ask: AskJSON & { breakdown?: unknown }
  if (kind === 'areas') {
    const ev = rooms.map((r: { id: string; name?: string; polygon: [number, number][] }) => ({ id: r.id, shape: 'polygon' as const, pts: r.polygon.map(flip), group: cat(r.name), value: r2(area(r.polygon)), unit: 'm2' }))
    const groups = [...new Set(ev.map((e: { group: string }) => e.group))] as string[]
    const breakdown = groups.map((g) => ({ group: g, value: r2(ev.filter((e: { group: string }) => e.group === g).reduce((a: number, e: { value: number }) => a + e.value, 0)), unit: 'm2' }))
    ask = { answer: r2(breakdown.reduce((a, b) => a + b.value, 0)), unit: 'm2', evidence: ev, breakdown, honesty: { tier: 'measured', basis: 'synthetic test answer' } }
  } else if (kind === 'baseboards') {
    const rs = rooms.filter((r: { name?: string }) => !/terrass|lift|treppe/i.test(r.name ?? ''))
    const ev = rs.map((r: { id: string; name?: string; polygon: [number, number][] }) => ({ id: r.id, shape: 'polyline' as const, pts: [...r.polygon, r.polygon[0]].map(flip), group: cat(r.name), value: r2(per(r.polygon)), unit: 'm' }))
    ask = { answer: r2(ev.reduce((a: number, e: { value: number }) => a + e.value, 0)), unit: 'm', evidence: ev, honesty: { tier: 'measured', basis: 'synthetic test answer' } }
  } else if (kind === 'slabs') {
    const units = sheet.units_list ?? []
    const ev = units.map((u: { id: string; polygon: [number, number][] }, i: number) => ({ id: u.id, shape: 'polygon' as const, pts: u.polygon.map(flip),
      group: i % 2 ? '150 slab, D13 @ 300' : '300 slab, D16 @ 300', thickness_m: i % 2 ? 0.15 : 0.3, value: r2(area(u.polygon)), unit: 'm2' }))
    ask = { answer: r2(ev.reduce((a: number, e: { value: number }) => a + e.value, 0)), unit: 'm2', evidence: ev, honesty: { tier: 'measured', basis: 'synthetic test answer' } }
  } else {
    const wet = rooms.filter((r: { name?: string }) => /bad|k[üu]che/i.test(r.name ?? ''))
    const c = (p: [number, number][]) => [p.reduce((a, q) => a + q[0], 0) / p.length, p.reduce((a, q) => a + q[1], 0) / p.length] as [number, number]
    const riser = c(wet[0].polygon)
    const ev = wet.map((r: { id: string; name?: string; polygon: [number, number][] }, i: number) => {
      const q = c(r.polygon)
      return { id: `pipe${i}`, shape: 'polyline' as const, pts: [q, [q[0], riser[1]], riser].map((p) => flip(p as [number, number])),
        group: i === 0 ? '4" riser' : '2" branch', diameter_m: i === 0 ? 0.1 : 0.05, value: r2(Math.abs(q[0] - riser[0]) + Math.abs(q[1] - riser[1])), unit: 'm' }
    })
    ask = { answer: r2(ev.reduce((a: number, e: { value: number }) => a + e.value, 0)), unit: 'm', evidence: ev, honesty: { tier: 'measured', basis: 'synthetic test answer' } }
  }
  view.showAnswer(ask, `Synthetic test (${kind}): not a real answer`)
}

async function main() {
  // ?path=<server path>[&ask=question]: any drawing the demo server knows, asked live (rotated PDF pages, gold sheets)
  const path = P.get('path')
  if (path) {
    await view.open(path)
    $('drawing').innerHTML = `<option>${esc(path)}</option>`
    const q = P.get('ask')
    if (q) {
      const r = await (await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path, question: q }) })).json()
      view.showAnswer(r, q); renderAnswer(r, null)
    }
    if (P.has('clean')) view.setClean(true, Number(P.get('clean') || 0.5))
    if (P.get('mode') === '3d') view.setMode('3d')
    return
  }
  // ?img=<url>: any image as a raster sheet (no backend data), e.g. the judges' example sheets in judge/
  const img = P.get('img')
  if (img) {
    const head = new DataView(await (await fetch(img, { headers: { Range: 'bytes=0-31' } })).arrayBuffer()) // PNG IHDR: width, height
    await view.load({ render: { type: 'image', width: head.getUint32(16), height: head.getUint32(20), url: img, source_type: 'raster' } })
    if (P.get('mode') === '3d') view.setMode('3d')
    $('drawing').innerHTML = `<option>${esc(img)}</option>`
    return
  }
  index = await getJSON('/fixtures/index.json')
  $('drawing').innerHTML = index.map((f) => `<option value="${esc(f.stem)}">${esc(f.title)}</option>`).join('')
  await openStem(P.get('d') ?? index[0].stem)
  if (P.has('q')) await showAsk(Number(P.get('q')))
  if (P.get('synthetic')) await synthetic(P.get('synthetic')!)
  if (P.get('sel')) view.select(P.get('sel'))
  if (P.get('mode') === '3d') view.setMode('3d')
  if (P.has('clean')) view.setClean(true, Number(P.get('clean') || 0.5)) // ?clean=0.5: clean view, divider position
  if (P.has('photo')) setTimeout(() => (document.querySelector('.dv-tools button[title^="Path"]') as HTMLButtonElement | null)?.click(), 1500)
}
main()
