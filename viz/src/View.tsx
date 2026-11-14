import { Suspense, useMemo, useRef, useSyncExternalStore } from 'react'
import { Canvas } from '@react-three/fiber'
import type { Answer, Drawing, Element } from './types'
import type { Store } from './store'
import { Scene, focusOf } from './Scene'
import { Crop } from './Crop'
import { bbox, outline } from './geom'

const TIER_TEXT: Record<string, string> = {
  read: 'Read from the sheet', measured: 'Measured from geometry', detected: 'Detected by a model',
  estimated: 'Estimated', 'cannot determine': 'Cannot determine',
}

export function View({ store }: { store: Store }) {
  const s = useSyncExternalStore(store.subscribe, store.get)
  const overlay = useRef<HTMLDivElement>(null)
  const photoStatus = useRef<HTMLSpanElement>(null)
  const { loaded, answer, selectedId } = s
  const selected = answer?.elements.find((e) => e.id === selectedId) ?? null
  const focus = useMemo(
    () => (selected ? focusOf([selected], 0.3) : answer?.status === 'answered' ? focusOf(answer.elements) : null),
    [selected, answer],
  )
  const select = (id: string | null) => store.set({ selectedId: id })

  return (
    <div className="dv-view">
      {loaded && (
        <Canvas flat shadows="percentage" dpr={[1, 2]} camera={{ fov: 30, near: 1, far: 1e6, position: [0, 1e4, 0] }} onPointerMissed={() => select(null)}>
          <color attach="background" args={['#1a1f2b']} />
          <Suspense fallback={null}>
            <Scene key={loaded.drawing.key} loaded={loaded} answer={answer} selectedId={selectedId} hoverId={s.hoverId}
              dim={s.dim} showPlan={s.showPlan} mode={s.mode} photo={s.photo && s.mode === '3d'} photoStatus={photoStatus} focus={focus} fitAll={s.fitAll}
              onSelect={select} onHover={(id) => store.set({ hoverId: id })} overlay={overlay} />
          </Suspense>
        </Canvas>
      )}
      <div ref={overlay} className="dv-overlay" />

      {answer && <AnswerBadge answer={answer} />}
      {selected && loaded && <Detail drawing={loaded.drawing} el={selected} onClose={() => select(null)} />}

      {loaded && (
        <div className="dv-tools">
          {answer?.status === 'answered' && answer.elements.length > 0 && (
            <button className={s.dim ? 'dv-on' : ''} onClick={() => store.set({ dim: !s.dim })}>Dim rest</button>
          )}
          {!answer && loaded.plan.length > 0 && (
            <button className={s.showPlan ? 'dv-on' : ''} onClick={() => store.set({ showPlan: !s.showPlan })}>Found plan</button>
          )}
          <button onClick={() => store.set({ selectedId: null, fitAll: s.fitAll + 1 })}>Fit sheet</button>
          {s.mode === '3d' && (
            <button className={s.photo ? 'dv-on' : ''} title="Path-traced render: refines while the camera rests" onClick={() => store.set({ photo: !s.photo })}>Photo</button>
          )}
          <button className={s.mode === '3d' ? 'dv-on' : ''} onClick={() => store.set({ mode: s.mode === '3d' ? '2d' : '3d', photo: false })}>
            {s.mode === '3d' ? '2D' : '3D'}
          </button>
        </div>
      )}
      {loaded && s.mode === '3d' && (
        <div className="dv-illustrative">3D for orientation only: wall heights are illustrative, answers come from the drawing{s.photo && <> · <span ref={photoStatus}>Path tracing…</span></>}</div>
      )}

      {s.loading && <div className="dv-status">Loading drawing…</div>}
      {s.error && <div className="dv-status dv-error">{s.error}</div>}
      {!loaded && !s.loading && !s.error && <div className="dv-status">Open a drawing to see it here.</div>}
    </div>
  )
}

function AnswerBadge({ answer }: { answer: Answer }) {
  if (answer.status === 'cannot_answer')
    return (
      <div className="dv-badge dv-cannot">
        <div className="dv-eyebrow">Can't answer from this drawing</div>
        {answer.question && <div className="dv-q">{answer.question}</div>}
        <p>{answer.explanation ?? 'The drawing does not contain what this question needs.'}</p>
      </div>
    )
  const off = answer.cited - answer.elements.length
  return (
    <div className="dv-badge">
      <div className="dv-value">{answer.value}</div>
      {answer.question && <div className="dv-q">{answer.question}</div>}
      <div className="dv-chips">
        {answer.tier && <span className={`dv-chip dv-tier-${answer.tier.replace(' ', '-')}`}>{TIER_TEXT[answer.tier] ?? answer.tier}</span>}
        {answer.deterministic === false && <span className="dv-chip">not deterministic</span>}
        {answer.basis && <span className="dv-chip">{answer.basis}</span>}
      </div>
      {(answer.groups.length > 1 || answer.groups.some((g) => g.value)) && (
        <ul className="dv-legend">
          {answer.groups.map((g) => (
            <li key={g.name}><i style={{ background: g.color }} /><span>{g.name}</span><b>{g.value ?? `${g.count}×`}</b></li>
          ))}
        </ul>
      )}
      {answer.cited > 0 && off > 0 && (
        <p className="dv-note">
          {answer.elements.length} of {answer.cited} shown on this sheet. The other {off} are elsewhere in the model, not on this drawing.
        </p>
      )}
      {answer.cited === 0 && <p className="dv-note">Nothing to highlight on the drawing for this answer.</p>}
      {answer.styleWarning && <p className="dv-warn">Unfamiliar drawing style: this count may be wrong.</p>}
    </div>
  )
}

const pct = (c?: number | null) => (c == null ? null : `${Math.round(c * 100)}%`)
const HOW: Record<string, string> = { polygon: 'outline', polyline: 'line', box: 'detection box', guid: 'BIM element', detector: 'detected', read: 'read from sheet' }

function Detail({ drawing, el, onClose }: { drawing: Drawing; el: Element; onClose: () => void }) {
  const b = bbox(outline(el))
  const [x0, y0] = drawing.fromPx([b.x0, b.y0]), [x1, y1] = drawing.fromPx([b.x1, b.y1])
  const unit = drawing.units === 'm' ? 'm' : drawing.units === 'px' ? 'px' : ''
  const f = (v: number) => (drawing.units === 'px' ? Math.round(v) : v.toFixed(2))
  return (
    <div className="dv-detail">
      <div className="dv-row">
        <div className="dv-eyebrow">Where this is on the original drawing</div>
        <button className="dv-x" onClick={onClose} aria-label="Close">×</button>
      </div>
      <Crop drawing={drawing} el={el} width={300} height={190} />
      <dl>
        {el.label && <><dt>Mark</dt><dd>{el.label}</dd></>}
        {el.name && <><dt>Name</dt><dd>{el.name}</dd></>}
        {el.group && <><dt>Group</dt><dd><i className="dv-swatch" style={{ background: el.color }} />{el.group}</dd></>}
        {el.valueText && <><dt>Value</dt><dd>{el.valueText}</dd></>}
        <dt>Type</dt><dd>{el.type === 'evidence' ? HOW[el.how ?? ''] ?? 'evidence' : el.type}</dd>
        {pct(el.confidence) && <><dt>Confidence</dt><dd>{pct(el.confidence)}</dd></>}
        <dt>ID</dt><dd><code>{el.id}</code></dd>
        <dt>Position</dt>
        <dd><code>x {f(Math.min(x0, x1))}–{f(Math.max(x0, x1))}, y {f(Math.min(y0, y1))}–{f(Math.max(y0, y1))}{unit && ' ' + unit}</code></dd>
      </dl>
    </div>
  )
}
