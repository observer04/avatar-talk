import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The Pipecat bot (server/app.py) listens on :7860. Proxying /api keeps the
// WebRTC signaling request same-origin in dev.
const BOT_URL = process.env.BOT_URL ?? 'http://localhost:7860'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: BOT_URL, changeOrigin: true },
    },
  },
})
