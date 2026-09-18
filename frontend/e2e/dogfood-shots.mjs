// Dogfood evidence: deterministic Playwright captures of the FNOL wizard
// and the claim-detail trace timeline (run with `node dogfood-shots.mjs`).
import { chromium } from '@playwright/test';

const BASE = 'http://localhost:3002';
const OUT = '/home/user/work/evidence';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

// Login
await page.goto(BASE + '/');
await page.waitForSelector('[data-testid="login-gate"]');
await page.fill('#login-email', 'dogfood-fnol@claimos.dev');
await page.fill('#login-password', 'dogfood-Passw0rd!42');
await page.getByRole('button', { name: /sign in/i }).click();
await page.waitForSelector('[data-testid="dashboard"]');

// Wizard: step 1 empty (before)
await page.goto(BASE + '/new-claim');
await page.waitForSelector('[data-testid="wizard-step-0"]');
await page.screenshot({ path: `${OUT}/fnol-wizard-step1-empty.png` });

// Fill incident step (after)
await page.selectOption('[data-testid="incident-type"]', 'accident');
await page.fill('[data-testid="incident-date"]', '2026-09-15');
await page.fill('[data-testid="estimated-cost"]', '4200');
await page.fill(
  '[data-testid="incident-description"]',
  'Rear-ended at a stop light on Elm Street; rear bumper and trunk damaged.'
);
await page.screenshot({ path: `${OUT}/fnol-wizard-step1-filled.png` });

// Policy holder step with live lookup (seeded policy)
await page.getByTestId('wizard-next-btn').click();
await page.waitForSelector('[data-testid="wizard-step-1"]');
await page.fill('[data-testid="holder-name"]', 'Dana Whitfield');
await page.fill('[data-testid="holder-email"]', 'dana.whitfield@example.com');
await page.selectOption('[data-testid="incident-role"]', 'policyholder');
await page.fill('[data-testid="policy-number-input"]', 'AUTO-2024-001847');
await page.waitForSelector('[data-testid="policy-found-panel"]', { timeout: 20000 });
await page.screenshot({ path: `${OUT}/fnol-wizard-step2-policy-found.png` });

// Claim detail of the failed run: trace timeline with error tile + tool calls
await page.goto(BASE + '/claims/CLM-20260918-003');
await page.waitForSelector('[data-testid="claim-detail"]', { timeout: 20000 });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${OUT}/fnol-claim-detail-trace-failed.png`, fullPage: true });

await browser.close();
console.log('dogfood screenshots captured');
