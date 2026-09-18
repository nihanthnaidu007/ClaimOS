import { test, expect } from '@playwright/test';
import { apiLogin, saveEvidence, seededAdjuster, waitForTerminal, uiLogin } from './utils';

// Forced agent failure: with FIXTURE_FAULT=timeout:intake the backend fixture
// adapter times out every LLM call; the worker burns the retry budget and
// marks the run failed with a customer-safe reason. The wizard shows the
// PROCESSING FAILED verdict and the adjuster surface carries the failure.

// Only meaningful against a fault-injected backend.
test.skip(
  process.env.FIXTURE_FAULT !== 'timeout:intake',
  'run against a stack booted with FIXTURE_FAULT=timeout:intake'
);

let adjuster;
let submitted = null;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  adjuster = seededAdjuster();
});

test('a pipeline that cannot reach its LLM fails visibly, not silently', async ({ page }) => {
  // The FNOL console is an adjuster surface — the inline policy lookup is
  // adjuster-only (require_adjuster), so the filer must be an adjuster.
  await uiLogin(page, adjuster);
  await page.goto('/new-claim');

  await page.selectOption('[data-testid="incident-type"]', 'theft');
  await page.fill('[data-testid="incident-date"]', '2026-09-09');
  await page.fill('[data-testid="estimated-cost"]', '2000');
  await page.fill(
    '[data-testid="incident-description"]',
    'Bicycle stolen overnight from the apartment bike room; police report filed.'
  );
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-1')).toBeVisible();
  await page.fill('[data-testid="holder-name"]', 'Dana Whitfield');
  await page.fill('[data-testid="holder-email"]', 'dana.whitfield@example.com');
  await page.selectOption('[data-testid="incident-role"]', 'policyholder');
  // 001847: this spec runs FIRST in CI (alphabetical) with FIXTURE_FAULT
  // set, so its claim is the policy's 1st of the suite; stp-approval later
  // relies on being only the 2nd (see utils.js).
  await page.fill('[data-testid="policy-number-input"]', 'AUTO-2024-001847');
  await expect(page.getByTestId('policy-found-panel')).toBeVisible({ timeout: 20_000 });
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-2')).toBeVisible();
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-3')).toBeVisible();

  const claimCreated = page
    .waitForResponse((r) => r.url().includes('/api/claims') && r.request().method() === 'POST')
    .then((r) => r.json());
  await page.getByTestId('submit-claim-btn').click();
  submitted = await claimCreated;
  expect(submitted.claimId).toBeTruthy();

  await expect(page.getByTestId('pipeline-board')).toBeVisible();
  // Every stage burns its retry budget against the dead adapter — the run
  // fails well inside this window in fixture mode.
  await expect(page.getByTestId('decision-panel')).toBeVisible({ timeout: 240_000 });
  await expect(page.getByTestId('decision-panel')).toContainText('PROCESSING FAILED');
  await saveEvidence(page, 'tc-3-agent-failure-wizard');
});

test('the failed run is recorded with a customer-safe reason', async ({ page, request }) => {
  expect(submitted?.claimId, 'failure test must have submitted a claim').toBeTruthy();
  const { token } = await apiLogin(request, adjuster);
  const claim = await waitForTerminal(request, token, submitted.claimId);
  expect(claim.status).toBe('failed');
  expect(claim.failure_reason || claim.failureReason, 'reason recorded').toBeTruthy();

  // The adjuster surface renders the same terminal state.
  await uiLogin(page, adjuster);
  await page.goto(`/workbench/claims/${submitted.claimId}`);
  await expect(page.getByTestId('case-view')).toBeVisible();
  await saveEvidence(page, 'tc-3-agent-failure-case');
});
