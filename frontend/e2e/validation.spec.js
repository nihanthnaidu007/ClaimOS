import { test, expect } from '@playwright/test';
import {
  apiLogin,
  authHeaders,
  registerUser,
  saveEvidence,
  theftClaim,
  uiLogin,
} from './utils';

// Validation layering: the wizard blocks malformed input client-side, the API
// re-validates server-side (422s), and a syntactically valid submission
// against an unknown policy is halted by the pipeline (policy leg) — the
// claim stays pending with the failure visible in the trace.

let adjuster;
let customer;

test.beforeAll(async ({ request }) => {
  adjuster = await registerUser(request, { role: 'adjuster' });
  customer = await registerUser(request, { role: 'customer' });
});

test('wizard blocks malformed incident input before any network submit', async ({
  page,
  request,
}) => {
  await uiLogin(page, customer);
  await page.goto('/new-claim');

  await page.getByTestId('wizard-next-btn').click();
  // Empty required fields: inline errors, still on the incident step.
  await expect(page.getByTestId('wizard-step-0')).toBeVisible();
  await expect(
    page.locator('[data-testid="wizard-step-0"] [class*="ef4444"]').first()
  ).toBeVisible();

  // Negative amount: blocked with an inline error, no step change.
  await page.selectOption('[data-testid="incident-type"]', 'theft');
  await page.fill('[data-testid="incident-date"]', '2026-09-14');
  await page.fill('[data-testid="estimated-cost"]', '-50');
  await page.fill(
    '[data-testid="incident-description"]',
    'Bicycle stolen overnight from the apartment bike room; police report filed.'
  );
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-0')).toBeVisible();

  // No claim was created while the wizard blocked the user.
  const { token } = await apiLogin(request, adjuster);
  const res = await request.get('/api/claims', { headers: authHeaders(token) });
  const claims = await res.json();
  const blockedSubmissions = claims.filter((c) => c.claimed_amount === -50);
  expect(blockedSubmissions, 'no claim row for blocked input').toEqual([]);
  await saveEvidence(page, 'tc-2-validation-blocked');
});

test('API rejects malformed submissions with 422 and a field error', async ({ request }) => {
  const { token } = await apiLogin(request, adjuster);
  const cases = [
    ['negative amount', theftClaim({ claimedAmount: -50 })],
    ['zero amount', theftClaim({ claimedAmount: 0 })],
    ['amount over cap', theftClaim({ claimedAmount: 5_000_001 })],
    ['bad date', theftClaim({ incidentDate: '09/15/2026' })],
    ['short description', theftClaim({ description: 'Too short.' })],
  ];
  for (const [label, payload] of cases) {
    const res = await request.post('/api/claims', {
      data: payload,
      headers: authHeaders(token),
    });
    expect(res.status(), label).toBe(422);
    const body = await res.json();
    expect(body.detail, label).toBeTruthy();
  }
});

test('valid submission against an unknown policy halts with a visible reason', async ({
  page,
  request,
}) => {
  const { token } = await apiLogin(request, adjuster);

  // A fully valid submission aims at a policy number that does not exist —
  // the schema accepts it and the pipeline's policy leg halts the run.
  const res = await request.post('/api/claims', {
    data: theftClaim({ policyNumber: 'AUTO-1999-000000' }),
    headers: authHeaders(token),
  });
  expect(res.status()).toBe(200);
  const { claimId } = await res.json();

  // The pipeline halts at the policy leg: status stays pending, the trace
  // records the policy miss, and the run is halted (not failed) so the
  // customer can fix and resubmit.
  const deadline = Date.now() + 60_000;
  let claim = null;
  while (Date.now() < deadline) {
    const get = await request.get(`/api/claims/${claimId}`, {
      headers: authHeaders(token),
    });
    claim = await get.json();
    if (claim.agent_trace?.policy) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  expect(claim?.agent_trace?.policy?.found, 'policy leg recorded').toBe(false);
  expect(claim.status).toBe('pending');

  // The adjuster-facing detail page renders the halted trace for the claim.
  await uiLogin(page, adjuster);
  await page.goto(`/claims/${claimId}`);
  await expect(page.getByTestId('claim-detail')).toBeVisible();
  await expect(page.getByTestId('trace-timeline')).toBeVisible();
  await saveEvidence(page, 'tc-2-policy-halt');
});
