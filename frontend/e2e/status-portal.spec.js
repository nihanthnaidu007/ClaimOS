import { test, expect } from '@playwright/test';
import {
  apiLogin,
  registerUser,
  saveEvidence,
  seededAdjuster,
  submitClaim,
  theftClaim,
  waitForTerminal,
  authHeaders,
  POLICY_400_DEDUCTIBLE,
} from './utils';

// Public status portal: claim number + access code is the only credential.
// Unknown claim, wrong code, and rate limit all render the same
// customer-facing messages, and a successful lookup shows the live timeline.

const UNIFORM_MISS =
  'No claim found for that claim number and access code. Double-check both values — the code is case-sensitive.';

let claim = null;

test.beforeAll(async ({ request }) => {
  const adjuster = seededAdjuster();
  const customer = await registerUser(request);

  const custToken = (await apiLogin(request, customer)).token;
  claim = await submitClaim(
    request,
    custToken,
    // 012001: portal claim must auto-approve (badge /approved/i), so it lands
    // as 2nd claim on its policy — the frequency rule (+25 risk at 3+/12mo,
    // self included) stays quiet (see utils.js).
    theftClaim({ incidentDate: '2026-09-12', policyNumber: POLICY_400_DEDUCTIBLE })
  );

  const adjToken = (await apiLogin(request, adjuster)).token;
  await waitForTerminal(request, adjToken, claim.claimId);
});

test('unknown claim and wrong code render the same uniform error', async ({ page }) => {
  await page.goto('/status');
  await expect(page.getByTestId('status-portal')).toBeVisible();

  // Unknown claim number. The code meets the 16-char schema minimum so the
  // request reaches the handler and 404s there instead of 422ing on
  // validation — uniformity must hold at the lookup, not the front door.
  await page.fill('[data-testid="status-claim-input"]', 'CLM-19700101-000');
  await page.fill('[data-testid="status-code-input"]', 'whatever-code-at-least-16');
  await page.getByTestId('status-lookup-submit').click();
  await expect(page.getByTestId('status-lookup-error')).toHaveText(UNIFORM_MISS);

  // Real claim number, wrong access code: identical message — existence is
  // not revealed.
  await page.fill('[data-testid="status-claim-input"]', claim.claimId);
  await page.fill('[data-testid="status-code-input"]', 'definitely-wrong');
  await page.getByTestId('status-lookup-submit').click();
  await expect(page.getByTestId('status-lookup-error')).toHaveText(UNIFORM_MISS);
  await expect(page.getByTestId('status-timeline')).toHaveCount(0);
  await saveEvidence(page, 'tc-8-portal-uniform-error');
});

test('the access code from submission unlocks the live status timeline', async ({ page }) => {
  await page.goto('/status');
  await page.fill('[data-testid="status-claim-input"]', claim.claimId);
  await page.fill('[data-testid="status-code-input"]', claim.accessCode);
  await page.getByTestId('status-lookup-submit').click();

  await expect(page.getByTestId('status-timeline')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('status-claim-number')).toContainText(claim.claimId);
  // F2 / AC-2.3: the "What happens next" card renders the projection's copy.
  // This claim is decided, so the card shows the decided line and carries no
  // ETA chip — nothing is pending, and an absent ETA must not render.
  await expect(page.getByTestId('next-steps-card')).toBeVisible();
  await expect(page.getByTestId('next-steps-list')).toContainText(
    /A decision has been made on your claim/
  );
  await expect(page.getByTestId('next-steps-list').locator('li')).not.toHaveCount(0);
  await expect(page.getByTestId('status-expected-resolution')).toHaveCount(0);
  // The fixture run finished: the badge reflects the persisted decision.
  await expect(page.getByTestId('status-badge')).toContainText(/approved/i);
  // Milestones of the completed run render as timeline entries.
  const milestones = page.locator('[data-testid^="milestone-"]');
  const count = await milestones.count();
  expect(count, 'milestones render').toBeGreaterThan(0);

  // F6 decision transparency: the deny-by-default projection renders the
  // "Why this decision" section — pre-written stage copy, the decision's
  // plain-language summary, and a customer citation.
  const transparency = page.getByTestId('decision-transparency');
  await expect(transparency).toBeVisible();
  await expect(transparency).toContainText('Verifying your coverage');
  await expect(transparency).toContainText('Running standard checks');
  await expect(page.getByTestId('decision-summary')).toContainText(/decision/i);
  await expect(page.getByTestId('decision-citation').first()).toBeVisible();

  // Deny-by-default at the surface: internal adjudication data has no path
  // into the customer portal payload.
  const rendered = await page.content();
  for (const marker of ['riskScore', 'adjustedPayout', 'fingerprint', 'letterBody']) {
    expect(rendered, `internal marker must not render: ${marker}`).not.toContain(marker);
  }
  await saveEvidence(page, 'tc-8-portal-transparency');
  await saveEvidence(page, 'tc-8-portal-timeline');
});

test('settlement recording surfaces the card and completes the final milestone', async ({
  page,
  request,
}) => {
  // AC-7.1 absence branch: the fixture run is decided but no settlement is
  // recorded yet — no card, and milestone 4 still pending.
  await page.goto('/status');
  await page.fill('[data-testid="status-claim-input"]', claim.claimId);
  await page.fill('[data-testid="status-code-input"]', claim.accessCode);
  await page.getByTestId('status-lookup-submit').click();
  await expect(page.getByTestId('status-timeline')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('settlement-card')).toHaveCount(0);
  await expect(page.getByText('3 of 4 milestones complete')).toBeVisible();

  // The adjuster records the settlement (record-only facts; no money moves).
  const adjToken = (await apiLogin(request, seededAdjuster())).token;
  const res = await request.post(`/api/claims/${claim.claimId}/settlement`, {
    data: { amount: 1150, method: 'bank_transfer', reference: 'E2E-SETTLE-1' },
    headers: authHeaders(adjToken),
  });
  expect(res.status(), await res.text()).toBe(201);

  // Re-lookup: the card appears with the recorded amount, and the final
  // milestone completes (Wave 0 fixed the payout_recorded emission).
  await page.getByTestId('status-lookup-submit').click();
  await expect(page.getByTestId('settlement-card')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('settlement-amount')).toContainText(/1,150|1150/);
  await expect(page.getByTestId('settlement-payment-timing')).toContainText(
    /settlement has been recorded/i
  );
  await expect(page.getByText('4 of 4 milestones complete')).toBeVisible();

  // AC-7.2 at the surface: the card renders only fields that exist on the
  // record — the payment method and reference never reach the portal.
  const settled = await page.content();
  for (const marker of ['bank_transfer', 'bank transfer', 'E2E-SETTLE-1']) {
    expect(settled, `settlement bookkeeping must not render: ${marker}`).not.toContain(marker);
  }
  await saveEvidence(page, 'tc-8-portal-settlement');
});

test('lookups past the per-IP limit hit the uniform rate-limit message', async ({
  page,
  request,
}) => {
  // Burn the per-IP budget over the API first (same source IP as the
  // browser). CI configures STATUS_LOOKUP_RATE_LIMIT=10/minute so this stays
  // a handful of requests; the cap keeps the loop bounded either way.
  let hitLimit = false;
  for (let i = 0; i < 30 && !hitLimit; i += 1) {
    const res = await request.post('/api/status/lookup', {
      data: { claimNumber: 'CLM-19700101-000', accessCode: 'wrong-code-wrong-code' },
    });
    if (res.status() === 429) hitLimit = true;
  }
  expect(hitLimit, 'rate limit eventually answers 429').toBe(true);

  // The browser form now gets the same treatment and renders the message.
  await page.goto('/status');
  await page.fill('[data-testid="status-claim-input"]', claim.claimId);
  await page.fill('[data-testid="status-code-input"]', claim.accessCode);
  await page.getByTestId('status-lookup-submit').click();
  await expect(page.getByTestId('status-lookup-error')).toContainText(/Too many attempts/i, {
    timeout: 20_000,
  });
  await saveEvidence(page, 'tc-8-portal-rate-limited');
});
