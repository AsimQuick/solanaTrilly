// ---
// file: frontend/vite.config.js
// stack: react+vite
// purpose: Vite build configuration for the solanatrilly React dashboard.
//   Dev server proxies API and WebSocket calls to the Django/Daphne 'web' container
//   so the frontend can be developed with full backend integration locally.
// created-by: dev-team
// sprint: sprint-10
// story: US-48 AC-48.1
// last-updated: 2026-06-17
// ---

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend URL: the 'web' container inside Docker Compose (django+daphne on port 8000)
const BACKEND_URL = process.env.VITE_BACKEND_URL || 'http://web:8000'
const WS_BACKEND_URL = BACKEND_URL.replace(/^http/, 'ws')

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Proxy API and WS to Django backend in dev (avoids CORS, mirrors production)
    proxy: {
      '/api': {
        target: BACKEND_URL,
        changeOrigin: true,
      },
      '/ws': {
        target: WS_BACKEND_URL,
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
