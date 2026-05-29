import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite dev server config:
// - port 5173 (default)
// - /api/* is proxied to the FastAPI backend on :8000 so the UI can
//   call fetch('/api/template-layouts') without thinking about CORS
//   during development.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
