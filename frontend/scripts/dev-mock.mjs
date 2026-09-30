// Cross-platform launcher for `npm run dev:mock`.
// Sets MOCK_API=1 (read by vite.config.js) and starts the Vite dev server,
// so no shell-specific env syntax (cross-env) is needed.
import { spawn } from 'node:child_process'

const child = spawn('npx', ['vite'], {
  stdio: 'inherit',
  shell: true,
  env: { ...process.env, MOCK_API: '1' },
})

child.on('exit', (code) => process.exit(code ?? 0))
