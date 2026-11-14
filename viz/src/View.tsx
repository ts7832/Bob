import { Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type PointerEvent as RPointerEvent, type RefObject } from 'react'
import { Canvas } from '@react-three/fiber'
import type { Answer, Drawing, Element, SheetView } from './types'
import type { Store } from './store'
import { Scene, focusOf, measureTotals, type Insets } from './Scene'
import { Crop } from './Crop'
import { bbox, outline } from './geom'
import { CLS_NAME, GLYPH, SYMBOL, type Cls } from './ink'
import { canClean, cleanClasses, SYMBOL_COLOR } from './clean'

const TIER_TEXT: Record<string, string> = {
  read: 'Read from the sheet', measured: 'Measured from geometry', detected: 'Detected by a model',
  estimated: 'Estimated', 'cannot determine': 'Cannot determine',
}

export function View({ store }: { store: Store }) {
  const s = useSyncExternalStore(store.subscribe, store.get)
  const overlay = useRef<HTMLDivElement>(null)
  const viewRef = useRef<HTMLDivElement>(null), badgeRef = useRef<HTMLDivElement>(null), detailRef = useRef<HTMLDivElement>(null)
  const photoStatus = useRef<HTMLSpanElement>(null)
  const { loaded, answer, selectedId } = s
  const selected = answer?.elements.find((e) => e.id === selectedId) ?? null
  const vf = loaded?.views.find((v) => v.id === s.viewFocus) ?? null
  const focus = useMemo(
    () => (selected ? focusOf([selected], 0.12)
      : vf ? { x0: vf.box[0], y0: vf.box[1], x1: vf.box[2], y1: vf.box[3], min: 0 }
      : answer?.status === 'answered' ? focusOf(answer.elements) : null),
    [selected, answer, vf],
  )
  const select = (id: string | null) => store.set({ selectedId: id })
  const cleanOk = useMemo(() => (loaded ? canClean(loaded) : false), [loaded])
  const low = s.quality === 'low' || (s.quality === 'auto' && s.slow)
  // Room the cards take, so zoom-to-answer frames the evidence in the free part of the view (not under a card)
  const [insets, setInsets] = useState<Insets>({ l: 0, t: 0, r: 0, b: 48 })
  useLayoutEffect(() => {
    const measure = () => {
      const v = viewRef.current?.getBoundingClientRect()
      if (!v) return
      const q = (n: number) => Math.max(0, Math.round(n / 8) * 8)
      const bd = badgeRef.current?.getBoundingClientRect(), dt = detailRef.current?.getBoundingClientRect()
      const wide = v.width > 900 // wide view: cards take a column at the side; narrow: a band at the top
      const next: Insets = {
        l: bd && wide ? q(bd.right - v.left + 12) : 0,
        t: bd && !wide ? q(bd.bottom - v.top + 12) : 0,
        r: dt && wide ? q(v.right - dt.left + 12) : 0,
        b: 48,
      }
      setInsets((p) => (p.l === next.l && p.t === next.t && p.r === next.r && p.b === next.b ? p : next))
    }
    measure()
    const ro = new ResizeObserver(measure)
    for (const n of [viewRef.current, badgeRef.current, detailRef.current]) if (n) ro.observe(n)
    return () => ro.disconnect()
  }, [answer, selected])
  // Keyboard, like takeoff tools: arrows step through the evidence, F frames it again, M measures, Space (held) shows
  // the untouched original, Esc clears the measurement / selection. Ignored while typing in the host page's inputs.
  useEffect(() => {
    // only when nothing else on the host page has focus (or the focus is inside the view): the page's own buttons,
    // inputs and lists keep Space / arrows (e.g. Space on a focused question button must click it, not peek)
    const typing = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (!t || t === document.body || t === document.documentElement) return false
      return !viewRef.current?.contains(t) || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable
    }
    const down = (e: KeyboardEvent) => {
      const st = store.get()
      if (typing(e) || !st.loaded) return
      if (e.key === 'Escape') {
        if (st.mpts.length) store.set({ mpts: [], mclosed: false })
        else if (st.measure) store.set({ measure: false })
        else if (st.selectedId) store.set({ selectedId: null })
      } else if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && st.answer?.status === 'answered') {
        e.preventDefault(); step(e.key === 'ArrowRight' ? 1 : -1)
      } else if (e.key === 'f' || e.key === 'F') store.set({ refit: st.refit + 1 })
      else if (e.key === 'm' || e.key === 'M') store.set({ measure: !st.measure, mpts: [], mclosed: false })
      else if ((e.key === 'c' || e.key === 'C') && st.mode === '2d' && canClean(st.loaded)) store.set({ clean: !st.clean })
      else if (e.key === ' ' && !e.repeat) { e.preventDefault(); store.set({ peek: true }) }
    }
    const up = (e: KeyboardEvent) => { if (e.key === ' ' && store.get().peek) store.set({ peek: false }) }
    window.addEventListener('keydown', down); window.addEventListener('keyup', up)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up) }
  }, [store])
  // step through the visible evidence (legend groups switched off are skipped)
  const step = (d: number) => {
    const st = store.get(), list = (st.answer?.elements ?? []).filter((e) => !e.group || !st.hidden.includes(e.group))
    if (!list.length) return
    const i = list.findIndex((e) => e.id === st.selectedId)
    const n = i < 0 ? (d > 0 ? 0 : list.length - 1) : (i + d + list.length) % list.length
    store.set({ selectedId: list[n].id })
  }

  return (
    <div className="dv-view" ref={viewRef}>
      {loaded && (
        <Canvas flat shadows="percentage" dpr={low ? 1 : [1, 2]} camera={{ fov: 30, near: 1, far: 1e6, position: [0, 1e4, 0] }} onPointerMissed={() => select(null)}>
          <color attach="background" args={['#f4f5f7']} />
          <Suspense fallback={null}>
            <Scene key={loaded.drawing.key} loaded={loaded} answer={answer} selectedId={selectedId} hoverId={s.hoverId}
              dim={s.dim} showPlan={s.showPlan} mode={s.mode} photo={s.photo && s.mode === '3d'} photoStatus={photoStatus} focus={focus} fitAll={s.fitAll} insets={insets}
              tools={{ view: vf, measure: s.measure, mpts: s.mpts, mclosed: s.mclosed, peek: s.peek, hidden: s.hidden, cut: s.cut, refit: s.refit, clean: s.clean && cleanOk, swipe: s.swipe }}
              onMeasure={(mpts, mclosed) => store.set({ mpts, mclosed })}
              low={low} onSlow={(slow) => { if (store.get().slow !== slow) store.set({ slow }) }}
              onSelect={select} onHover={(id) => store.set({ hoverId: id })} overlay={overlay} />
          </Suspense>
        </Canvas>
      )}
      <div ref={overlay} className="dv-overlay" />

      {answer && <AnswerBadge answer={answer} boxRef={badgeRef} selectedId={selectedId} hidden={s.hidden} onStep={step} frameNote={loaded?.drawing.frameNote} frameBad={loaded?.drawing.frameBad}
        onToggle={(g) => store.set({ hidden: s.hidden.includes(g) ? s.hidden.filter((x) => x !== g) : [...s.hidden, g], selectedId: null })} />}
      {selected && loaded && <Detail drawing={loaded.drawing} el={selected} onClose={() => select(null)} boxRef={detailRef} />}
      {!selected && selectedId && answer?.citedIds.includes(selectedId) && (
        <div className="dv-detail" ref={detailRef}>
          <div className="dv-row"><div className="dv-title">Not on this drawing</div>
            <button className="dv-x" onClick={() => select(null)} aria-label="Close">×</button></div>
          <p className="dv-offsheet">This element is part of the answer (it is in the building model) but this sheet does not draw it,
            for example because it is on another storey. <code>{selectedId}</code></p>
        </div>
      )}

      {loaded && (
        <div className="dv-tools">
          {answer?.status === 'answered' && answer.elements.length > 0 && (
            <button className={s.dim ? 'dv-on' : ''} onClick={() => store.set({ dim: !s.dim })}>Dim rest</button>
          )}
          {!answer && loaded.plan.length > 0 && (
            <button className={s.showPlan ? 'dv-on' : ''} onClick={() => store.set({ showPlan: !s.showPlan })}>Found plan</button>
          )}
          {loaded.views.length >= 2 && (
            <select className="dv-views" value={s.viewFocus ?? ''} title="This sheet holds several drawings: frame one of them"
              onChange={(e) => store.set({ viewFocus: e.target.value || null, selectedId: null, ...(e.target.value ? {} : { fitAll: s.fitAll + 1 }) })}>
              <option value="">All {loaded.views.length} drawings</option>
              {loaded.views.map((v, i) => <option key={v.id} value={v.id}>{viewLabel(v, i)}</option>)}
            </select>
          )}
          <button title="Clear the selection and frame the sheet (the building in 3D)" onClick={() => store.set({ selectedId: null, viewFocus: null, fitAll: s.fitAll + 1 })}>Reset view</button>
          {cleanOk && s.mode === '2d' && (
            <button className={s.clean ? 'dv-on' : ''} title="The plan redrawn from what was detected, next to the original: drag the divider to compare (C)"
              onClick={() => store.set({ clean: !s.clean })}>Clean view</button>
          )}
          <button className={s.measure ? 'dv-on' : ''} title="Measure lengths and areas on the drawing (M)" onClick={() => store.set({ measure: !s.measure, mpts: [], mclosed: false })}>Measure</button>
          <button title="Hold to see the original drawing without any highlights (or hold Space)"
            onPointerDown={() => store.set({ peek: true })} onPointerUp={() => store.set({ peek: false })} onPointerLeave={() => s.peek && store.set({ peek: false })}>Original</button>
          {s.mode === '3d' && (
            <button className={s.photo ? 'dv-on' : ''} title="Path-traced render: refines while the camera rests" onClick={() => store.set({ photo: !s.photo })}>Photo</button>
          )}
          <button className={s.mode === '3d' ? 'dv-on' : ''} onClick={() => store.set({ mode: s.mode === '3d' ? '2d' : '3d', photo: false })}>
            {s.mode === '3d' ? '2D' : '3D'}
          </button>
          {s.mode === '3d' && (
            <label className="dv-cut" title="Cut the walls lower to see into the rooms (illustrative heights)">Walls
              <input type="range" min={20} max={100} value={Math.round(s.cut * 100)} onChange={(e) => store.set({ cut: Number(e.target.value) / 100 })} />
            </label>
          )}
        </div>
      )}
      {loaded && s.mode === '3d' && (
        <div className="dv-illustrative">3D for orientation only: wall heights are illustrative, answers come from the drawing{s.photo && <> · <span ref={photoStatus}>Path tracing…</span></>}</div>
      )}

      {loaded && cleanOk && s.clean && s.mode === '2d' && !s.peek && <Swipe value={s.swipe} host={viewRef} classes={cleanClasses(loaded)} onChange={(swipe) => store.set({ swipe })} />}
      {loaded && s.measure && <MeasureReadout drawing={loaded.drawing} pts={s.mpts} closed={s.mclosed} />}
      {s.peek && <div className="dv-peek">Original drawing</div>}
      {s.loading && <div className="dv-status">Loading drawing…</div>}
      {s.error && <div className="dv-status dv-error">{s.error}</div>}
      {!loaded && !s.loading && !s.error && <div className="dv-status">Open a drawing to see it here.</div>}
    </div>
  )
}

// Swipe divider of the clean view: redrawn plan on the left, the untouched original on the right.
function Swipe({ value, host, classes, onChange }: { value: number; host: RefObject<HTMLDivElement | null>; classes: Cls[]; onChange: (v: number) => void }) {
  const move = (e: RPointerEvent) => {
    const r = host.current?.getBoundingClientRect()
    if (r) onChange(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)))
  }
  return (
    <div className="dv-swipe" style={{ left: `${value * 100}%` }}>
      <div className="dv-swipe-line" />
      <div className="dv-swipe-handle" role="slider" aria-label="Clean view divider" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)} tabIndex={0}
        onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); e.stopPropagation() }}
        onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) move(e) }}
        onKeyDown={(e) => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); e.stopPropagation(); onChange(Math.min(1, Math.max(0, value + (e.key === 'ArrowLeft' ? -0.05 : 0.05)))) } }}>‹ ›</div>
      <div className="dv-swipe-l" title="Walls, rooms and symbols as the system detected or read them (scans: walls found in the image). Check them against the original.">
        <b>Redrawn from detections</b>
        {classes.length > 0 && <span className="dv-swipe-key">{classes.map((c) => (
          <span key={c} style={{ color: SYMBOL_COLOR[c] }}><Glyph cls={c} />{CLS_NAME[c][1]}</span>
        ))}</span>}
      </div>
      <div className="dv-swipe-r">Original</div>
    </div>
  )
}

const viewLabel = (v: SheetView, i: number) => {
  const t = `${i + 1}. ${v.title ?? 'Drawing'}`
  return (t.length > 44 ? t.slice(0, 42) + '…' : t) + (v.scale ? ` · ${v.scale}` : '')
}

// Known technical backend messages in plain words; the original stays in the tooltip (and in the chat).
function friendly(t?: string | null) {
  if (!t) return 'The drawing does not contain what this question needs.'
  if (/language model (is )?(not available|switched off)|ModuleNotFoundError|DRAWQA_LLM|no ANTHROPIC_API_KEY/i.test(t))
    return 'This question needs the language model, which is not running in this demo. Try asking with the wording of a listed question.'
  return t
}

function AnswerBadge({ answer, boxRef, selectedId, hidden, onStep, onToggle, frameNote, frameBad }: {
  answer: Answer; boxRef: RefObject<HTMLDivElement | null>; selectedId: string | null; hidden: string[]; frameNote?: string | null; frameBad?: boolean
  onStep: (d: number) => void; onToggle: (group: string) => void
}) {
  if (answer.status === 'cannot_answer')
    return (
      <div className="dv-badge dv-cannot" ref={boxRef}>
        <div className="dv-title">Can't answer this from the drawing</div>
        {answer.question && <div className="dv-q">{answer.question}</div>}
        <p title={answer.explanation ?? undefined}>{friendly(answer.explanation)}</p>
      </div>
    )
  const off = answer.cited - answer.elements.length
  const visible = answer.elements.filter((e) => !e.group || !hidden.includes(e.group)), pos = visible.findIndex((e) => e.id === selectedId)
  const [num, ...rest] = (answer.value ?? '').split(' '), unit = rest.join(' ') // '278.9 m²' -> big number, small unit (page's .big)
  return (
    <div className="dv-badge" ref={boxRef}>
      <div className="dv-value">{num}{unit && <small>{unit}</small>}</div>
      {answer.question && <div className="dv-q">{answer.question}</div>}
      <div className="dv-chips">
        {answer.tier && <span className={`dv-chip dv-tier-${answer.tier.replace(' ', '-')}`}>{TIER_TEXT[answer.tier] ?? answer.tier}</span>}
        {answer.deterministic === false && <span className="dv-chip">not deterministic</span>}
        <KindChips els={answer.elements} />
      </div>
      {answer.basis && <p className="dv-basis">{answer.basis}</p>}
      {(answer.groups.length > 1 || answer.groups.some((g) => g.value)) && (
        <ul className="dv-legend">
          {answer.groups.map((g) => (
            <li key={g.name} className={hidden.includes(g.name) ? 'dv-off' : ''} onClick={() => onToggle(g.name)} title="Show or hide this group">
              <i style={{ background: g.color }} /><span>{g.name}</span><b>{g.value ?? `${g.count}×`}</b>
            </li>
          ))}
        </ul>
      )}
      {answer.elements.length > 1 && (
        <div className="dv-step">
          <button onClick={() => onStep(-1)} aria-label="Previous item">‹</button>
          <span>{pos >= 0 ? `${pos + 1} of ${visible.length}` : `${visible.length} items`}</span>
          <button onClick={() => onStep(1)} aria-label="Next item">›</button>
          <em>check each one with ← →</em>
        </div>
      )}
      {answer.cited > 0 && off > 0 && (
        <p className="dv-note">
          {answer.elements.length} of {answer.cited} shown on this sheet. The other {off} are elsewhere in the model, not on this drawing.
        </p>
      )}
      {answer.cited === 0 && <p className="dv-note">Nothing to highlight on the drawing for this answer.</p>}
      {!!answer.outside && (
        <p className="dv-warn">{frameBad
          ? `The answer's positions do not fit this page (a rotated PDF page?), so its ${answer.outside} item${answer.outside > 1 ? 's are' : ' is'} not highlighted. Check the answer against the drawing by hand.`
          : `${answer.outside} cited item${answer.outside > 1 ? 's lie' : ' lies'} off this page and ${answer.outside > 1 ? 'are' : 'is'} not drawn.`}</p>
      )}
      {frameNote && answer.elements.length > 0 && <p className="dv-note">{frameNote}</p>}
      {answer.styleWarning && <p className="dv-warn">Unfamiliar drawing style: this count may be wrong.</p>}
      {answer.views && answer.views.length === 1 && <p className="dv-from">From the drawing <b>{answer.views[0].title}</b></p>}
      {answer.views && answer.views.length > 1 && (
        <p className="dv-note">The evidence comes from {answer.views.length} drawings on this sheet: {answer.views.map((v) => `${v.title} (${v.n})`).join(', ')}.
          Check that they show different things, not the same floor twice.</p>
      )}
      {!!answer.dupes?.length && (
        <p className="dv-warn">Same outline cited twice: items {answer.dupes.slice(0, 3).join(', ')}{answer.dupes.length > 3 ? ` and ${answer.dupes.length - 3} more` : ''}. Check whether one element was counted twice.</p>
      )}
    </div>
  )
}

// What the highlighted symbols are (one chip per class, with counts when they differ: "2 showers, 1 bathtub")
function KindChips({ els }: { els: Element[] }) {
  const n = new Map<Cls, number>()
  for (const e of els) if (e.cls && SYMBOL.has(e.cls as Cls)) n.set(e.cls as Cls, (n.get(e.cls as Cls) ?? 0) + 1)
  if (!n.size) return null
  return <>{[...n].map(([c, k]) => (
    <span key={c} className="dv-chip dv-kind"><Glyph cls={c} />{n.size > 1 ? `${k} ` : ''}{CLS_NAME[c][n.size > 1 && k === 1 ? 0 : 1]}</span>
  ))}</>
}

export const Glyph = ({ cls }: { cls?: string | null }) => {
  const d = cls ? GLYPH[cls as Cls] : undefined
  return d ? <svg className="dv-glyph" viewBox="0 0 16 16" aria-hidden="true"><path d={d} /></svg> : null
}

const pct = (c?: number | null) => (c == null ? null : `${Math.round(c * 100)}%`)
const HOW: Record<string, string> = { polygon: 'outline', polyline: 'line', box: 'detection box', guid: 'BIM element', detector: 'detected', read: 'read from sheet' }

function Detail({ drawing, el, onClose, boxRef }: { drawing: Drawing; el: Element; onClose: () => void; boxRef: RefObject<HTMLDivElement | null> }) {
  const b = bbox(outline(el))
  const [x0, y0] = drawing.fromPx([b.x0, b.y0]), [x1, y1] = drawing.fromPx([b.x1, b.y1])
  const unit = drawing.units === 'm' ? 'm' : drawing.units === 'px' ? 'px' : ''
  const f = (v: number) => (drawing.units === 'px' ? Math.round(v) : v.toFixed(2))
  return (
    <div className="dv-detail" ref={boxRef}>
      <div className="dv-row">
        <div className="dv-title">Where this is on the original drawing</div>
        <button className="dv-x" onClick={onClose} aria-label="Close">×</button>
      </div>
      <Crop drawing={drawing} el={el} width={300} height={190} />
      <dl>
        {el.label && <><dt>Mark</dt><dd>{el.label}</dd></>}
        {el.name && <><dt>Name</dt><dd>{el.name}</dd></>}
        {el.group && <><dt>Group</dt><dd><i className="dv-swatch" style={{ background: el.color }} />{el.group}</dd></>}
        {el.valueText && <><dt>Value</dt><dd>{el.valueText}</dd></>}
        {el.cls && el.cls !== 'other' && <><dt>Symbol</dt><dd className="dv-kind"><Glyph cls={el.cls} />{CLS_NAME[el.cls as Cls]?.[0] ?? el.cls}</dd></>}
        <dt>Type</dt><dd>{el.type === 'evidence' ? HOW[el.how ?? ''] ?? 'evidence' : el.type}</dd>
        {pct(el.confidence) && <><dt>Confidence</dt><dd>{pct(el.confidence)}</dd></>}
        <dt>ID</dt><dd><code>{el.id}</code></dd>
        <dt>Position</dt>
        <dd><code>x {f(Math.min(x0, x1))} to {f(Math.max(x0, x1))}, y {f(Math.min(y0, y1))} to {f(Math.max(y0, y1))}{unit && ' ' + unit}</code></dd>
      </dl>
    </div>
  )
}

function MeasureReadout({ drawing, pts, closed }: { drawing: Drawing; pts: [number, number][]; closed: boolean }) {
  const t = measureTotals(drawing, pts, closed)
  const fmt = (v: number) => (t.sc.unit === 'px' ? Math.round(v).toLocaleString() : v.toFixed(2))
  return (
    <div className="dv-measure">
      {pts.length < 2
        ? <span>Click points on the drawing. Click the first point again to measure an area. Esc clears.</span>
        : <span><b>{fmt(t.length)} {t.sc.unit}</b>{closed ? ' perimeter' : ' length'}{t.area != null && <> · <b>{fmt(t.area)} {t.sc.areaUnit}</b> area</>}</span>}
      <small>{t.sc.source}</small>
    </div>
  )
}
