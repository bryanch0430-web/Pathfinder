import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// The dev server proxies "/api" (HTTP and WebSocket) to the backend, so the browser talks to a
// single origin and no CORS configuration is needed. The default target is the IPv4 loopback
// address on purpose: on Node 18 "localhost" resolves to ::1 first, while uvicorn listens on
// 127.0.0.1 by default, which makes the proxy fail with ECONNREFUSED ::1:8000. Override with
// VITE_DEV_PROXY_TARGET if the backend runs elsewhere.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [vue()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': { target, changeOrigin: true, ws: true },
      },
    },
  }
})
