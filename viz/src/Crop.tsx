import { useEffect, useRef } from 'react'
import type { Drawing, Element } from './types'
import { bbox, outline } from './geom'

// The element cut straight out of the original drawing at full resolution, with its outline drawn on top.
export function Crop({ drawing, el, width, height, pad = 0.35 }: {
  drawing: Drawing; el: Element; width: number; height: number; pad?: number
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const src = drawing.source
    // the source image can be larger than the sheet frame (e.g. a PDF page rendered at higher DPI)
    const sw = src instanceof HTMLImageElement ? src.naturalWidth : src.width
    const s0 = sw / drawing.width
    const dpr = window.devicePixelRatio || 1
    c.width = width * dpr; c.height = height * dpr
    const g = c.getContext('2d')!
    const pts = outline(el)
    const b = bbox(pts)
    const minSide = Math.max(drawing.width, drawing.height) * 0.01
    let w = Math.max(b.w, minSide) * (1 + 2 * pad), h = Math.max(b.h, minSide) * (1 + 2 * pad)
    if (w / h > width / height) h = (w * height) / width; else w = (h * width) / height
    const x0 = b.cx - w / 2, y0 = b.cy - h / 2, s = c.width / w
    g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height)
    g.drawImage(src, x0 * s0, y0 * s0, w * s0, h * s0, 0, 0, c.width, c.height)
    g.beginPath()
    pts.forEach(([x, y], i) => (i ? g.lineTo : g.moveTo).call(g, (x - x0) * s, (y - y0) * s))
    g.closePath()
    g.fillStyle = 'rgba(255,106,0,0.10)'; g.fill()
    g.lineWidth = 2 * dpr; g.strokeStyle = '#ff6a00'; g.stroke()
  }, [drawing, el, width, height, pad])
  return <canvas ref={ref} style={{ width, height }} className="dv-crop" />
}
