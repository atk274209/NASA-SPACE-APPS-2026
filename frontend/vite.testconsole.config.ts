import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// AM Test Console: a separate application on its own origin (port 5174), independent of the
// main app on 5173. It talks to the same backend (FastAPI on :8000), which must run with
// AM_TEST_MODE=1 for /api/test/* to exist.
//
// Browsers send cookies to every port of a host, so the proxy marks each request with
// X-AM-Client: test-console. The backend then keeps this console's session in its own cookie
// (am_tc_session), and signing in or out here never touches the main app's session.

// Serve the console's own page (testconsole/index.html) at "/".
const consoleIndex: Plugin = {
  name: 'am-test-console-index',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url === '/' || req.url === '/index.html' || req.url?.startsWith('/?')) req.url = '/testconsole/index.html'
      next()
    })
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss(), consoleIndex],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        // keep the browser's Host so the backend's same-origin write check matches Origin
        changeOrigin: false,
        headers: { 'X-AM-Client': 'test-console' },
      },
    },
  },
  build: { outDir: 'dist-testconsole', emptyOutDir: true, rollupOptions: { input: 'testconsole/index.html' } },
})
