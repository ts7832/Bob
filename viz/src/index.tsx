// Public API, bundled as one IIFE script (window.DrawView). Drop-in for demo/index.html's open/showPlan/showEvidence/clearHl/resetView.
//
//   const view = DrawView.mount(document.getElementById("viewer"))
//   await view.open(path)                  // fetches /api/render, /api/plan, /api/sheet itself
//   view.showAnswer(askResponse, question) // the /api/ask JSON as-is
//   view.clear(); view.reset(); view.setMode("2d" | "3d"); view.onSelect(el => ...); view.destroy()
import { createRoot } from 'react-dom/client'
import css from './styles.css?inline'
import { prepare, toAnswer } from './adapter'
import { Store } from './store'
import { View } from './View'
import type { AskJSON, Element, Loaded, PlanJSON, RenderJSON, SheetJSON } from './types'

export interface MountOptions {
  base?: string // URL prefix for /api/* (default: same origin)
}

export interface DrawViewHandle {
  open(path: string): Promise<void>
  load(data: { render: RenderJSON; plan?: PlanJSON | null; sheet?: SheetJSON | null; imageUrl?: string }): Promise<void>
  showAnswer(ask: AskJSON, question?: string | null): void
  clear(): void
  reset(): void
  select(id: string | null): void
  setMode(mode: '2d' | '3d'): void
  setQuality(q: 'auto' | 'high' | 'low'): void // auto (default) drops AO/shadows/resolution when 3D runs slow
  setClean(on: boolean, swipe?: number): void // clean view (2D): redrawn plan left of the divider, original right; swipe 0..1
  onSelect(cb: (el: Element | null) => void): () => void
  onLoad(cb: (info: { render: RenderJSON; plan: PlanJSON | null; sheet: SheetJSON | null }) => void): () => void
  destroy(): void
}

function injectCss() {
  if (document.getElementById('dv-styles')) return
  const s = document.createElement('style')
  s.id = 'dv-styles'
  s.textContent = css
  document.head.appendChild(s)
}

export function mount(el: HTMLElement, opts: MountOptions = {}): DrawViewHandle {
  injectCss()
  const base = opts.base ?? ''
  const store = new Store()
  const host = document.createElement('div')
  host.className = 'dv-root'
  el.appendChild(host)
  const root = createRoot(host)
  root.render(<View store={store} />)

  const loadSubs = new Set<(i: { render: RenderJSON; plan: PlanJSON | null; sheet: SheetJSON | null }) => void>()
  let seq = 0
  let pending: [AskJSON, string | null | undefined] | null = null // an answer that arrived before its drawing finished loading

  // demo/index.html's open() empties #viewer (`v.innerHTML = ""`): put the view back instead of silently vanishing
  const ensureAttached = () => { if (!host.isConnected) el.appendChild(host) }

  const getJSON = async (u: string) => {
    const r = await fetch(base + u)
    if (!r.ok) throw new Error(`${u.split('?')[0]}: HTTP ${r.status}`)
    const j = await r.json()
    if (j && j.error) throw new Error(j.error)
    return j
  }

  const apply = (loaded: Loaded) => {
    store.set({ loaded, answer: null, selectedId: null, hoverId: null, loading: false, error: null, mpts: [], mclosed: false, hidden: [], viewFocus: null, inspected: null })
    if (pending) { const [a, q] = pending; pending = null; handle.showAnswer(a, q) }
  }

  const handle: DrawViewHandle = {
    async open(path) {
      ensureAttached()
      const s = ++seq
      pending = null
      store.set({ loading: true, error: null, answer: null, selectedId: null })
      try {
        const q = encodeURIComponent(path)
        // one after the other, never in parallel: /api/plan and /api/sheet both build the same sheet on the server, and
        // two threads opening the same IFC model at once crash ifcopenshell (segfault, seen 19:50 on main). In sequence the
        // second request is served from the server's cache, so it is not slower.
        const render = await getJSON(`/api/render?path=${q}`)
        if (s !== seq) return
        const plan = await getJSON(`/api/plan?path=${q}`).catch(() => null) // overlay and 3D context are optional
        if (s !== seq) return
        const sheet = await getJSON(`/api/sheet?path=${q}`).catch(() => null)
        if (s !== seq) return
        const loaded = await prepare(render, plan, sheet, render.url ? base + render.url : undefined)
        if (s !== seq) return
        apply(loaded)
        loadSubs.forEach((f) => f({ render, plan, sheet }))
      } catch (e) {
        if (s === seq) store.set({ loading: false, error: String((e as Error).message ?? e), loaded: null })
      }
    },
    async load({ render, plan = null, sheet = null, imageUrl }) {
      ensureAttached()
      const s = ++seq
      pending = null
      store.set({ loading: true, error: null, answer: null, selectedId: null })
      try {
        const loaded = await prepare(render, plan, sheet, imageUrl)
        if (s !== seq) return
        apply(loaded)
        loadSubs.forEach((f) => f({ render, plan, sheet }))
      } catch (e) {
        if (s === seq) store.set({ loading: false, error: String((e as Error).message ?? e), loaded: null })
      }
    },
    showAnswer(ask, question) {
      ensureAttached()
      const { loaded, loading } = store.get()
      if (!loaded || loading) { pending = [ask, question]; return }
      store.set({ answer: toAnswer(ask, loaded, question), selectedId: null, hoverId: null, hidden: [], viewFocus: null, inspected: null })
    },
    clear() { pending = null; store.set({ answer: null, selectedId: null, hoverId: null, inspected: null }) },
    reset() { store.set({ selectedId: null, fitAll: store.get().fitAll + 1 }) },
    select(id) { store.set({ selectedId: id }) },
    setMode(mode) { store.set({ mode }) },
    setQuality(quality) { store.set({ quality }) },
    setClean(clean, swipe) { store.set({ clean, ...(swipe != null ? { swipe: Math.min(1, Math.max(0, swipe)) } : {}) }) },
    onSelect(cb) { store.selectHandlers.add(cb); return () => { store.selectHandlers.delete(cb) } },
    onLoad(cb) { loadSubs.add(cb); return () => { loadSubs.delete(cb) } },
    destroy() { seq++; root.unmount(); host.remove() },
  }
  return handle
}
