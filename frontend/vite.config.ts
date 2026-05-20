import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/chat': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // FIX: /report was missing from the proxy so PDF download calls were
      // not forwarded to the backend in local dev, and the static build on
      // DigitalOcean had no route to forward them either.
      '/report': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/applications': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/validate-login': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    }
  }
})
