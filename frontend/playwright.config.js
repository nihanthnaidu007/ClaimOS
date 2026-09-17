import { defineConfig } from '@playwright/test';

// E2E against a native mirror of the compose stack: MongoDB (27017), API
// (:8002), durable worker, Vite dev server (:3001). Docker is unavailable in
// this environment, so the compose topology is mirrored as tmux-managed
// processes; the service contract (ports, proxying, env) matches compose.
export default defineConfig({
  testDir: './e2e',
  timeout: 240_000, // submission runs the real 5-agent pipeline
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
