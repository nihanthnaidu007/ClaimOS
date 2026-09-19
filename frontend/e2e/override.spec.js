import { test, expect } from '@playwright/test';
import {
  apiLogin,
  authHeaders,
  registerUser,
  saveEvidence,
  seededAdjuster,
  submitClaim,
  accidentClaim,
  waitForTerminal,
  uiLogin,
  POLICY_400_DEDUCTIBLE,
} from './utils';

// Adjuster override path: an escalated claim (STP gate refused
// straight-through) is reviewable; overriding it without a reason is a 422
// at the API and client-mirrored in the modal, and a reasoned override lands
// as an immutable audit entry and flips the claim to overridden.

let adjuster;
let customer;
let claimId = null;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  adjuster = seededAdjuster();
  customer = await registerUser(request);

  // $4,200 accident -> elevated severity -> the STP gate refuses
  // straight-through -> the run escalates -> the claim is reviewable.
  // 012001: keeps this claim off 001847/008899 so those slates stay under the
  // 3+ claims/12mo frequency flag for the approval-asserting specs (utils.js).
  const custToken = (await apiLogin(request, customer)).token;
  const submitted = await submitClaim(
    request,
    custToken,
    accidentClaim({ incidentDate: '2026-09-13', policyNumber: POLICY_400_DEDUCTIBLE })
  );
  claimId = submitted.claimId;

  const adjToken = (await apiLogin(request, adjuster)).token;
  const claim = await waitForTerminal(request, adjToken, claimId);
  expect(claim.status, 'fixture run escalates the accident claim').toBe('escalated');
});

test('override without a reason is a 422 and changes nothing', async ({ request }) => {
  const { token } = await apiLogin(request, adjuster);

  for (const reason of ['', '   ']) {
    const res = await request.post(`/api/workbench/claims/${claimId}/override`, {
      data: { reason },
      headers: authHeaders(token),
    });
    expect(res.status(), `blank reason "${reason}"`).toBe(422);
  }

  // No state change: the claim is still escalated with its original verdict.
  const res = await request.get(`/api/claims/${claimId}`, { headers: authHeaders(token) });
  const claim = await res.json();
  expect(claim.status).toBe('escalated');
});

test('reasoned override from the workbench records an audit entry', async ({ page, request }) => {
  await uiLogin(page, adjuster);
  await page.goto('/workbench');
  await expect(page.getByTestId('workbench-queue')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId(`queue-row-${claimId}`).click();
  await expect(page.getByTestId('case-view')).toBeVisible();

  // The modal mirrors the backend rule client-side: submit stays disabled
  // until the reason has content.
  await page.getByTestId('open-override').click();
  await expect(page.getByTestId('override-modal')).toBeVisible();
  await expect(page.getByTestId('override-submit')).toBeDisabled();
  await page.fill('[data-testid="override-reason"]', '   ');
  await expect(page.getByTestId('override-submit')).toBeDisabled();

  await page.fill('[data-testid="override-payout"]', '2600');
  await page.fill(
    '[data-testid="override-reason"]',
    'Comparable market estimates support a higher repair payout than the pipeline calculated.'
  );
  await expect(page.getByTestId('override-submit')).toBeEnabled();
  await page.getByTestId('override-submit').click();
  await expect(page.getByTestId('override-modal')).not.toBeVisible({ timeout: 30_000 });

  // The audit trail records actor, before/after, and the reason verbatim.
  await expect(page.getByTestId('audit-trail')).toBeVisible();
  await expect(page.getByTestId('audit-entry').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('audit-entry').first()).toContainText(
    'Comparable market estimates'
  );
  await saveEvidence(page, 'tc-5-override-audit');

  // Persisted: status flipped to overridden and the audit endpoint agrees.
  const { token } = await apiLogin(request, adjuster);
  const claim = await request
    .get(`/api/claims/${claimId}`, { headers: authHeaders(token) })
    .then((r) => r.json());
  expect(claim.status).toBe('overridden');
  const audit = await request
    .get(`/api/workbench/claims/${claimId}/audit`, { headers: authHeaders(token) })
    .then((r) => r.json());
  // The endpoint returns a bare array; unwrap only object envelopes.
  const entries = Array.isArray(audit) ? audit : audit.entries ?? [];
  expect(entries.length, 'exactly one audit entry').toBe(1);
  expect(entries[0].action).toBe('override');
  expect(entries[0].after?.payoutAmount).toBe(2600);
});
