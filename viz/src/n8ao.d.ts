// n8ao ships no typings; only what Model.tsx uses.
declare module 'n8ao' {
  import type { Camera, Scene } from 'three'
  import { Pass } from 'postprocessing'
  export class N8AOPostPass extends Pass {
    constructor(scene: Scene, camera: Camera, width?: number, height?: number)
    configuration: {
      aoRadius: number; distanceFalloff: number; intensity: number; halfRes: boolean
      transparencyAware: boolean; gammaCorrection: boolean; screenSpaceRadius: boolean; accumulate: boolean
      color: import('three').Color
    }
    setQualityMode(mode: 'Performance' | 'Low' | 'Medium' | 'High' | 'Ultra'): void
  }
}
