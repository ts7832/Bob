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
  return (await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: fx.path, question }) })).json()
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
  if (live) { const r = await postAsk(q); view.showAnswer(r, q); renderAnswer(r, null); return }
  const i = asks.findIndex((a) => a.question.toLowerCase() === q.toLowerCase())
  if (i >= 0) showAsk(i); else $('answer').innerHTML = '<div class="ans muted">Replay mode only knows the listed questions. Add ?live=1 for the real server.</div>'
})

async function main() {
  index = await getJSON('/fixtures/index.json')
  $('drawing').innerHTML = index.map((f) => `<option value="${esc(f.stem)}">${esc(f.title)}</option>`).join('')
  await openStem(P.get('d') ?? index[0].stem)
  if (P.has('q')) await showAsk(Number(P.get('q')))
  if (P.get('sel')) view.select(P.get('sel'))
  if (P.get('mode') === '3d') view.setMode('3d')
  if (P.has('photo')) setTimeout(() => (document.querySelector('.dv-tools button[title^="Path"]') as HTMLButtonElement | null)?.click(), 1500)
}
main()
