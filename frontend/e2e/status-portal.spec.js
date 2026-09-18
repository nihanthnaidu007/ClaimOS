import { test, expect } from '@playwright/test';
import {
  apiLogin,
  registerUser,
  saveEvidence,
  submitClaim,
  theftClaim,
  waitForTerminal,
} from './utils';

// Public status portal: claim number + access code is the only credential.
// Unknown claim, wrong code, and rate limit all render the same
// customer-facing messages, and a successful lookup shows the live timeline.

const UNIFORM_MISS =
  'Lookup failed. Please check your details and try again.';

let claim = null;

test.beforeAll(async ({ request }) => {
  const adjuster = await registerUser(request, { role: 'adjuster' });
  const customer = await registerUser(request, { role: 'customer' });

  const custToken = (await apiLogin(request, customer)).token;
  claim = await submitClaim(request, custToken, theftClaim({ incidentDate: '2026-09-12' }));

  const adjToken = (await apiLogin(request, adjuster)).token;
  await waitForTerminal(request, adjToken, claim.claimId);
});

test('unknown claim and wrong code render the same uniform error', async ({ page }) => {
  await page.goto('/status');
  await expect(page.getByTestId('status-portal')).toBeVisible();

  // Unknown claim number.
  await page.fill('[data-testid="status-claim-input"]', 'CLM-19700101-000');
  await page.fill('[data-testid="status-code-input"]', 'whatever-code');
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
  // The fixture run finished: the badge reflects the persisted decision.
  await expect(page.getByTestId('status-badge')).toContainText(/approved/i);
  // Milestones of the completed run render as timeline entries.
  const milestones = page.locator('[data-testid^="milestone-"]');
  const count = await milestones.count();
  expect(count, 'milestones render').toBeGreaterThan(0);
  await saveEvidence(page, 'tc-8-portal-timeline');
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
