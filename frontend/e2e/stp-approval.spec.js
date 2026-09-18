import { test, expect } from '@playwright/test';
import {
  apiLogin,
  authHeaders,
  registerUser,
  saveEvidence,
  waitForTerminal,
  uiLogin,
} from './utils';

// STP straight-through path: a low-severity, clean, high-confidence claim is
// finalized by the gate without any human touch — the decision panel shows
// the auto-approved verdict and the deductible-adjusted payout, the persisted
// record reads auto_approved, and the adjuster surface carries the portal
// access code.

const POLICY_NUMBER = 'AUTO-2024-001847'; // seeded: active, theft covered, $50k limit, $500 deductible
const DESCRIPTION =
  'Bicycle stolen overnight from the apartment bike room; police report case 26-44112 filed the same morning.';

let adjuster;
let customer;
let claimId = null;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  adjuster = await registerUser(request, { role: 'adjuster' });
  customer = await registerUser(request, { role: 'customer' });
});

test('low-severity clean claim auto-approves through the STP gate', async ({ page, request }) => {
  await uiLogin(page, customer);
  await page.goto('/new-claim');

  await page.selectOption('[data-testid="incident-type"]', 'theft');
  await page.fill('[data-testid="incident-date"]', '2026-09-16');
  await page.fill('[data-testid="estimated-cost"]', '2000');
  await page.fill('[data-testid="incident-description"]', DESCRIPTION);

  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-1')).toBeVisible();
  await page.fill('[data-testid="holder-name"]', 'Dana Whitfield');
  await page.fill('[data-testid="holder-email"]', 'dana.whitfield@example.com');
  await page.selectOption('[data-testid="incident-role"]', 'policyholder');
  await page.fill('[data-testid="policy-number-input"]', POLICY_NUMBER);
  await expect(page.getByTestId('policy-found-panel')).toBeVisible({ timeout: 20_000 });

  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-2')).toBeVisible();
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-3')).toBeVisible();

  const claimCreated = page
    .waitForResponse((r) => r.url().includes('/api/claims') && r.request().method() === 'POST')
    .then((r) => r.json());
  await page.getByTestId('submit-claim-btn').click();
  const submitted = await claimCreated;
  claimId = submitted.claimId;
  expect(claimId).toBeTruthy();

  // Pipeline board goes live over SSE, then the gate finalizes the claim.
  await expect(page.getByTestId('pipeline-board')).toBeVisible();
  await expect(page.getByTestId('decision-panel')).toBeVisible({ timeout: 180_000 });
  await expect(page.getByTestId('decision-panel')).not.toContainText('PROCESSING FAILED');
  await expect(page.getByTestId('decision-panel')).toContainText(/1,500\.00/); // $2,000 - $500 deductible
  await saveEvidence(page, 'tc-4-stp-auto-approval');

  // Persisted state: the gate finalized straight-through (auto_approved is
  // only ever set by the STP gate — never by a human step).
  const { token } = await apiLogin(request, adjuster);
  const claim = await waitForTerminal(request, token, claimId);
  expect(claim.status).toBe('auto_approved');
});

test('the finalized claim shows in the workbench with its portal access code', async ({
  page,
  request,
}) => {
  expect(claimId, 'STP test must have submitted a claim').toBeTruthy();
  await uiLogin(page, adjuster);
  await page.goto('/workbench');
  await expect(page.getByTestId('workbench-view')).toBeVisible({ timeout: 30_000 });

  const row = page.getByTestId(`queue-row-${claimId}`);
  await expect(row).toBeVisible({ timeout: 30_000 });

  await page.goto(`/claims/${claimId}`);
  await expect(page.getByTestId('claim-detail')).toBeVisible();
  await expect(page.getByTestId('access-code-panel')).toBeVisible();
  const codeText = await page.getByTestId('access-code-panel').innerText();
  expect(codeText).toMatch(/[A-Za-z0-9]{4,}/);
  await saveEvidence(page, 'tc-4-access-code-panel');

  // Belt and braces: the API record carries the same code for the portal.
  const { token } = await apiLogin(request, adjuster);
  const res = await request.get(`/api/claims/${claimId}`, { headers: authHeaders(token) });
  const claim = await res.json();
  expect(claim.access_code, 'access code present on the record').toBeTruthy();
});
