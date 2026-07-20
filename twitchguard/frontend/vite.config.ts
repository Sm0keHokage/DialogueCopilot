import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/auth': backend,
      '/channels': { target: backend, ws: true },
      '/healthz': backend,
    },
  },
})
