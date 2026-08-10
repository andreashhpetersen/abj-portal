import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// /api is proxied to Django so the browser sees a single origin in development.
// That keeps the session and CSRF cookies working exactly as they do in
// production, where both are served from the same host.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
