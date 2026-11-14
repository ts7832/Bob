import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const DEMO = 'http://localhost:8765' // the team's demo server (Bob/demo/server.py)

// dev: harness (index.html) on fixtures, or ?live=1 through the proxy
// build: one IIFE file dist/drawview.js exposing window.DrawView (React, three, CSS all inside)
export default defineConfig(({ command }) => ({
  plugins: [react()],
  publicDir: false,
  server: { proxy: { '/api': DEMO, '/page': DEMO, '/data': DEMO, '/file': DEMO } },
  define: command === 'build' ? { 'process.env.NODE_ENV': '"production"' } : {},
  build: {
    lib: { entry: 'src/index.tsx', name: 'DrawView', formats: ['iife'], fileName: () => 'drawview.js' },
    outDir: 'dist',
    emptyOutDir: true,
    cssCodeSplit: false,
  },
}))
