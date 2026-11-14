import type { Answer, Element, Loaded, Pt } from './types'

export interface State {
  loaded: Loaded | null
  answer: Answer | null
  selectedId: string | null
  hoverId: string | null
  mode: '2d' | '3d'
  photo: boolean // path-traced photo mode (3D only)
  quality: 'auto' | 'high' | 'low' // auto drops AO, shadows and resolution when 3D runs slow
  slow: boolean // set by the performance monitor
  measure: boolean // measure tool on (click points on the drawing)
  mpts: Pt[] // measured points, sheet px
  mclosed: boolean // the measured line was closed into an area
  peek: boolean // overlays hidden while held: the untouched original drawing
  hidden: string[] // answer groups switched off in the legend
  cut: number // 3D wall cut height, fraction of a storey (section)
  refit: number // bumped by F: frame the current selection / answer again
  dim: boolean
  showPlan: boolean
  fitAll: number // bumped by reset(): fit the whole sheet
  loading: boolean
  error: string | null
}

// Minimal external store: the public API writes, React reads through useSyncExternalStore.
export class Store {
  state: State = { loaded: null, answer: null, selectedId: null, hoverId: null, mode: '2d', photo: false, quality: 'auto', slow: false, measure: false, mpts: [], mclosed: false, peek: false, hidden: [], cut: 1, refit: 0, dim: true, showPlan: true, fitAll: 0, loading: false, error: null }
  private subs = new Set<() => void>()
  selectHandlers = new Set<(el: Element | null) => void>()

  get = () => this.state
  subscribe = (fn: () => void) => { this.subs.add(fn); return () => { this.subs.delete(fn) } }
  set = (patch: Partial<State>) => {
    const prevSel = this.state.selectedId
    this.state = { ...this.state, ...patch }
    this.subs.forEach((f) => f())
    if (this.state.selectedId !== prevSel) {
      const el = this.state.answer?.elements.find((e) => e.id === this.state.selectedId) ?? null
      this.selectHandlers.forEach((h) => h(el))
    }
  }
}
