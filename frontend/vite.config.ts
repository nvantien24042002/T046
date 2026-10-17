import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
export default defineConfig({
 plugins: [react()],
 server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy: { '/api/browser/v1': { target: 'http://127.0.0.1:8011', changeOrigin: true }, '/health': { target: 'http://127.0.0.1:8011', changeOrigin: true } } },
 test: { maxWorkers: 2, environment: 'jsdom', setupFiles: ['./src/tests/setup.ts'], clearMocks: true },
})
