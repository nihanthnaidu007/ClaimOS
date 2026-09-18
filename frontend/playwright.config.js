import { defineConfig } from '@playwright/test';

// E2E against a native mirror of the compose stack: MongoDB (27017), API
// (:8002), durable worker, Vite dev server (:3001). Docker is unavailable in
// this environment, so the compose topology is mirrored as tmux-managed
// processes; the service contract (ports, proxying, env) matches compose.
export default defineConfig({
  testDir: './e2e',
  // The submission test rides the REAL pipeline: main's tuned prompts miss
  // the typed schemas on the first pass often enough that stages burn 2-3
  // LLM round-trips (the adapter's bounded schema retry rescues them), so a
  // full run can legitimately take 5+ minutes. 540s > the 480s decision
  // wait inside the test.
  timeout: 540_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:3002',
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
