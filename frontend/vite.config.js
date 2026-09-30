import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import { mockApi } from './mock-api.js'

// MOCK_API=1 serves a local fake backend (see mock-api.js) so the dashboard can
// be developed without the real server. Off by default; never in a build.
const useMock = process.env.MOCK_API === '1'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), tailwindcss(), ...(useMock ? [mockApi()] : [])],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: true, // reachable from a phone on the same LAN
  },
})
