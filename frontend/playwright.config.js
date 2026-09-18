import { defineConfig } from '@playwright/test';

// E2E for the full ClaimOS stack. Two supported topologies:
//
// 1. Docker Compose (CI + any Docker host): `docker compose up -d --build`
//    with LLM_PROVIDER=fixture — the deterministic provider that keeps the
//    pipeline scripted with zero API spend. Point E2E_BASE_URL at the nginx
//    tier (default http://localhost:3000).
// 2. Native mirror (Docker-less dev sandboxes): MongoDB, uvicorn API,
//    `python worker.py`, and the Vite dev server run as separate processes
//    with the same service contract; set E2E_BASE_URL to the Vite origin.
//
// The suite never needs an Anthropic key: fixture mode covers the happy
// paths, and FIXTURE_FAULT=<kind>:<agent> replays live-LLM failure classes
// for the failure-path specs (which self-skip unless that fault is set).
export default defineConfig({
  testDir: './e2e',
  // The submission test rides the REAL pipeline: stages run through the
  // durable worker over Mongo. Fixture mode completes in seconds, but the
  // 480s decision wait inside the test keeps native-anthropic runs viable
  // for local debugging; CI always uses fixture mode.
  timeout: 540_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  // Deterministic suite: no retries. A retry would silently mask a flake
  // this pass exists to find (e.g. the live schema-miss wart).
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
