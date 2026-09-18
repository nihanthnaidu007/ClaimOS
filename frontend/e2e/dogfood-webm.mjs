// Dogfood evidence: live FNOL submission with a Playwright video recording
// of the pipeline board streaming (run with `node dogfood-webm.mjs`).
import { chromium } from '@playwright/test';

const BASE = 'http://localhost:3002';
const OUT = '/home/user/work/evidence';

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
});
const page = await context.newPage();

await page.goto(BASE + '/');
await page.waitForSelector('[data-testid="login-gate"]');
await page.fill('#login-email', 'dogfood-fnol@claimos.dev');
await page.fill('#login-password', 'dogfood-Passw0rd!42');
await page.getByRole('button', { name: /sign in/i }).click();
await page.waitForSelector('[data-testid="dashboard"]');

// Walk the wizard to a real submission; the video captures the whole flow.
await page.goto(BASE + '/new-claim');
await page.waitForSelector('[data-testid="wizard-step-0"]');
await page.selectOption('[data-testid="incident-type"]', 'accident');
await page.fill('[data-testid="incident-date"]', '2026-09-17');
await page.fill('[data-testid="estimated-cost"]', '3800');
await page.fill(
  '[data-testid="incident-description"]',
  'Sideswiped in a parking garage on Oak Avenue; driver door dented and mirror cracked.'
);
await page.getByTestId('wizard-next-btn').click();
await page.waitForSelector('[data-testid="wizard-step-1"]');
await page.fill('[data-testid="holder-name"]', 'Dana Whitfield');
await page.fill('[data-testid="holder-email"]', 'dana.whitfield@example.com');
await page.selectOption('[data-testid="incident-role"]', 'policyholder');
await page.fill('[data-testid="policy-number-input"]', 'AUTO-2024-001847');
await page.waitForSelector('[data-testid="policy-found-panel"]', { timeout: 20000 });
await page.getByTestId('wizard-next-btn').click();
// Coverage step → review step, then submit (the submit button lives on step 3).
await page.getByTestId('wizard-next-btn').click();
await page.waitForSelector('[data-testid="wizard-step-3"]', { timeout: 15000 });
await page.getByTestId('submit-claim-btn').click();

// Submission view: the pipeline board streams live events; hold the recording
// long enough for several agent stages to arrive in real time.
await page.waitForSelector('[data-testid="pipeline-board"]', { timeout: 30000 });
await page.waitForTimeout(90_000);
await page.screenshot({ path: `${OUT}/fnol-submission-live-board.png` });

await context.close();
await browser.close();
console.log('dogfood live-submission video captured');
