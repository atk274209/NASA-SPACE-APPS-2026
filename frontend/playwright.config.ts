import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from '@playwright/test'

const here = path.dirname(fileURLToPath(import.meta.url))

// End-to-end tests run against a throwaway AM instance:
//  - fresh temporary database seeded with the FICTIONAL test crew (5 astronauts + doctor)
//  - the API serving the built console (run `npm run build` first; `npm run e2e` does)
//  - the local language model deliberately unreachable, so every AI path uses its
//    deterministic fallback (this is also the offline-operation test)
// The installed Microsoft Edge is used, so no browser download is needed.
process.env.AM_E2E_DIR ??= path.join(os.tmpdir(), `am-e2e-${process.pid}`)
const dataDir = process.env.AM_E2E_DIR
const PORT = 8100

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1, // tests share one seeded database and change it
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: 'msedge',
    trace: 'retain-on-failure',
  },
  webServer: {
    // The fictional test crew with easy passwords ("<username>-test"), in a throwaway database.
    command: `uv run python -m am.cli seed-crew --simple-passwords && uv run uvicorn am.main:app --host 127.0.0.1 --port ${PORT}`,
    cwd: path.resolve(here, '..', 'backend'),
    url: `http://127.0.0.1:${PORT}/api/v1/system/status`,
    timeout: 240_000,
    reuseExistingServer: false,
    env: {
      AM_DATA_DIR: dataDir,
      AM_DATABASE_URL: `sqlite:///${path.join(dataDir, 'e2e.sqlite3').replaceAll('\\', '/')}`,
      AM_OLLAMA_URL: 'http://127.0.0.1:9',
      AM_TEST_ACCOUNTS_FILE: path.join(dataDir, 'TEST_CREDENTIALS.txt'), // never the developer's own file
      AM_PASSWORD_HASH_PROFILE: 'fast',
      AM_TEST_MODE: '1',
      AM_DEMO_EVENTS: '0', // random demo events would change what the tests see; tests trigger them explicitly
    },
  },
})
