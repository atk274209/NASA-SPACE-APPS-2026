import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// The API runs locally (FastAPI on :8000). The dev server proxies /api so the browser
// talks to a single origin and the backend's Host allow-list stays strict.
// The Test Console (frontend/testconsole, port 5174) is a separate application. This server
// refuses to serve its files, so test controls can never appear inside the main app.
const noTestConsole: Plugin = {
  name: 'am-no-test-console',
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      if (req.url?.startsWith('/testconsole') || req.url?.startsWith('/src/testconsole')) {
        res.statusCode = 404
        res.end('The Test Console runs on its own port (5174).')
        return
      }
      next()
    })
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss(), noTestConsole],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false /* keep Host = Origin for the backend's same-origin check */ },
    },
  },
})
