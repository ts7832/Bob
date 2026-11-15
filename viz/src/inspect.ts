// Every object the system recognised on this sheet, so any one can be hovered (its name), clicked (zoom + a card that
// says what it is and where that came from) without asking a question. Same sources as the 3D models:
//   scans / PDF pages : the detector's boxes (/api/plan), with their confidence
//   Swiss CAD         : openings and fixtures read from the CAD layers (/api/sheet)
//   BIM               : the model's own elements (IFC class + name)
import type { Element, Loaded } from './types'
import { normCls, type Cls } from './ink'

const SCAN_SYMBOLS = new Set(['door', 'window', 'wc', 'sink', 'bathtub', 'shower', 'sauna_stove', 'sauna_bench'])
const BIM_CLASSES = /^Ifc(Door|Window|Stair|StairFlight|Railing|FurnishingElement|Furniture|SanitaryTerminal|Column)$/
const NICE: Record<string, string> = {
  wc: 'WC', sauna_stove: 'Sauna stove', sauna_bench: 'Sauna bench', kitchen_unit: 'Kitchen unit', washbasin: 'Washbasin',
  washing_machine: 'Washing machine', other: 'Fixture (unlabelled)', IfcFurnishingElement: 'Furniture', IfcSanitaryTerminal: 'Sanitary fixture',
  IfcStairFlight: 'Stair flight',
}
export const niceName = (raw: string) => NICE[raw] ?? (raw.startsWith('Ifc') ? raw.slice(3) : raw.charAt(0).toUpperCase() + raw.slice(1).replace(/_/g, ' '))

const cache = new WeakMap<Loaded, Element[]>()
export function inspectables(l: Loaded): Element[] {
  const hit = cache.get(l)
  if (hit) return hit
  const out: Element[] = []
  const add = (id: string, raw: string, polygon: Element['polygon'], how: string, extra: Partial<Element> = {}) => {
    if (polygon.length < 3) return
    out.push({ id, type: raw, geometry: 'polygon', polygon, name: niceName(raw), how, cls: normCls(raw) as Cls | null, color: '#2f6fde', ...extra })
  }
  const d = l.drawing
  if (d.kind === 'svg') {
    for (const e of l.guids.values()) if (BIM_CLASSES.test(e.type)) add(e.id, e.type, e.ext ?? e.polygon, 'from the building model (IFC)', { value: e.name ?? null, leaf: e.leaf ?? null })
  } else if (d.kind === 'vector') {
    for (const p of l.context.parts ?? []) add(p.id, p.cls, p.polygon, 'read from the CAD drawing')
  } else {
    for (const p of l.plan) if (SCAN_SYMBOLS.has(p.type))
      add(p.id, p.type, p.polygon, 'detected by a model', { confidence: p.confidence ?? null })
  }
  cache.set(l, out)
  return out
}
