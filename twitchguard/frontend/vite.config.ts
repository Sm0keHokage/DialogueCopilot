import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/auth': backend,
      // Trailing slash on purpose: the SPA itself owns the bare "/account"
      // route (Личный кабинет). Every real backend endpoint in this
      // namespace has a segment after the slash (/account/login, /account/
      // register, ...), so scoping the proxy this way lets a hard reload of
      // "/account" fall through to Vite's SPA history fallback instead of
      // hitting the API and getting a raw JSON 404 back.
      '/account/': backend,
      '/channels': { target: backend, ws: true },
      '/healthz': backend,
    },
  },
})
