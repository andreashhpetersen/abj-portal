import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// These paths are proxied to Django so the browser sees a single origin in
// development. That keeps the session and CSRF cookies working exactly as they
// do in production, where everything is served from the same host.
//
// /admin and /static belong to Django too — without them the SPA's catch-all
// route would swallow the admin and send you back to the calendar, and the
// admin's own CSS would 404.
const DJANGO = { target: 'http://127.0.0.1:8000', changeOrigin: false }

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': DJANGO,
      '/admin': DJANGO,
      '/static': DJANGO,
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
