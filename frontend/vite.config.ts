import fs from 'node:fs'
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

const MAPLIBRE_WORKER = path.resolve(import.meta.dirname, 'node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs')
const MAPLIBRE_WORKER_PATH = 'maplibre-gl-worker.mjs'

/**
 * MapLibre parses vector tiles in a self-contained module Web Worker that it loads relative to its own
 * module URL. Vite's dependency pre-bundling moves the main module (breaking that relative URL), and Vite's
 * dev transform would inject its HMR client into the worker. Serving the worker file verbatim - in dev and
 * as a build asset - avoids both; the app points MapLibre at it with setWorkerUrl().
 */
function maplibreWorker(): Plugin {
  return {
    name: 'maplibre-worker',
    configureServer(server) {
      server.middlewares.use(`/${MAPLIBRE_WORKER_PATH}`, (_req, res) => {
        res.setHeader('Content-Type', 'text/javascript')
        fs.createReadStream(MAPLIBRE_WORKER).pipe(res)
      })
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: MAPLIBRE_WORKER_PATH, source: fs.readFileSync(MAPLIBRE_WORKER) })
    },
  }
}

// The dev server proxies API calls to the FastAPI backend, so the browser sees one origin (no CORS in dev).
// In production the frontend is static files; VITE_API_BASE_URL points at the API (see docs/deployment).
const apiTarget = process.env.VITE_DEV_API_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss(), maplibreWorker()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      ['/api', '/health', '/ready', '/docs', '/openapi.json'].map((p) => [p, { target: apiTarget, changeOrigin: true }]),
    ),
  },
})
