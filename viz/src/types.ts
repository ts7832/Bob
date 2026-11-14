// The view's internal model. Everything is in "sheet pixels": pixels of the texture the view draws, y pointing down.
// The adapter (adapter.ts) converts the demo's render / plan / sheet / ask JSON into this, whatever the source frame was.

export type Pt = [number, number]

export type Tier = 'read' | 'measured' | 'detected' | 'estimated' | 'cannot determine'

export interface Drawing {
  key: string // changes whenever a different drawing is loaded
  kind: 'image' | 'svg' | 'vector'
  width: number // sheet pixels
  height: number
  source: HTMLImageElement | HTMLCanvasElement // the drawing itself at full resolution: texture + verification crops
  toPx: (p: Pt) => Pt // source coordinates (render / evidence frame) -> sheet pixels
  fromPx: (p: Pt) => Pt // sheet pixels -> source coordinates, for showing positions
  units: 'px' | 'm' | 'svg' // units of the source coordinates
  mPerPx: number | null // real-world metres per sheet pixel, when the source has units
  note?: string
}

export interface Element {
  id: string
  type: string // door, window, wc, room, IfcSpace, ...
  geometry: 'polygon' | 'box' | 'point'
  polygon: Pt[] // sheet pixels; box = 4 corners; point = one point
  label?: string | null // short label shown on the drawing (count index, name)
  name?: string | null // longer name for the detail card
  value?: string | null
  confidence?: number | null
  how?: string | null // detector | linework | read | inferred | outline | exact | guid
}

export interface Answer {
  question?: string | null
  status: 'answered' | 'cannot_answer'
  value: string | null // formatted answer with unit, e.g. "193.48 m²"
  isCount: boolean
  tier: Tier | null
  deterministic?: boolean | null
  basis?: string | null
  explanation?: string | null
  method?: string | null
  styleWarning?: boolean
  elements: Element[] // evidence that could be placed on this sheet
  cited: number // evidence items in the answer, including ones not on this sheet
}

// Geometry for the 3D lift (M3). Display only; never used to answer anything.
export interface Context {
  walls: Pt[][]
  rooms: { id: string; name?: string | null; polygon: Pt[] }[]
}

export interface Loaded {
  drawing: Drawing
  plan: Element[] // what the backend found on the sheet (detector boxes etc.), drawn faintly
  context: Context
  guids: Map<string, Element> // svg sheets: every element with a data-guid, for evidence lookup
}

// Raw responses from the demo server, typed only as far as the view reads them.
export interface RenderJSON {
  type: 'image' | 'svg' | 'vector' | 'none'
  width?: number; height?: number; url?: string // image
  svg?: string // svg
  items?: VectorItem[]; bounds?: [number, number, number, number] // vector
  source_type?: string; note?: string
}
export type VectorItem =
  | { k: 'poly'; layer: string; pts: Pt[]; closed?: boolean }
  | { k: 'circle'; layer: string; c: Pt; r: number }
  | { k: 'arc'; layer: string; c: Pt; r: number; a0: number; a1: number }
  | { k: 'text'; layer: string; p: Pt; h: number; t: string }

export interface PlanJSON {
  drawable?: boolean
  items?: { type: string; how?: string; label?: string | null; conf?: number | null; pts?: Pt[]; bbox?: number[]; area_m2?: number | null }[]
  fit?: { level?: string; note?: string }
}

export interface SheetJSON {
  units?: string
  y_flip?: boolean
  scale?: { m_per_px?: number | null }
  rooms?: { id: string; name?: string | null; number?: string | null; polygon?: Pt[]; bbox?: number[] }[]
  walls?: { id: string; polygon?: Pt[] }[]
}

export interface AskJSON {
  answer?: number | string | null
  unit?: string | null
  explanation?: string | null
  method?: string | null
  confidence?: number | null
  evidence?: { id?: string; shape: 'polygon' | 'box' | 'guid'; pts?: Pt[]; bbox?: number[]; label?: string | null }[]
  honesty?: { tier?: Tier; deterministic?: boolean; basis?: string; style_warning?: boolean }
  error?: string
}
