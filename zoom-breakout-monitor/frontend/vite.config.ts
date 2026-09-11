import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the SPA runs on :5173 and the API on :8000. Proxying keeps
// the browser on a single origin so the HttpOnly session cookie is sent with
// both REST calls and the WebSocket upgrade, exactly as in production.
const backend = process.env.VITE_BACKEND_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/ws': { target: backend, ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
