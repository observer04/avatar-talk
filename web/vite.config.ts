import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The control-plane API (services/api) listens on :8000; `npm run mock` serves a
// stand-in on the same port. Proxying keeps API calls same-origin in dev.
const API_TARGET = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  // The lazily loaded TalkingHead chunk (Three.js) is ~0.9 MB by itself.
  build: { chunkSizeWarningLimit: 1000 },
  server: {
    proxy: {
      '/v1': { target: API_TARGET, changeOrigin: true },
      '/health': { target: API_TARGET, changeOrigin: true },
    },
  },
})
