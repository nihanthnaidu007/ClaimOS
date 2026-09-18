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
let claimAccessCode = null;

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
  claimAccessCode = submitted.accessCode;

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

// F14 reopen flow: a decided claim goes back under review through the audited
// reopen action — no pipeline re-run, the portal learns it is being reviewed
// again, and an explicit second decision replaces the first.
test('reopening the decided claim returns it to review, then a second decision replaces the first', async ({
  page,
  request,
}) => {
  await uiLogin(page, adjuster);
  await page.goto('/workbench');
  await expect(page.getByTestId('workbench-queue')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId(`queue-row-${claimId}`).click();
  await expect(page.getByTestId('case-view')).toBeVisible();

  // Decided state: reopen is the offered action, not Record decision.
  await expect(page.getByTestId('open-reopen')).toBeVisible();
  await expect(page.getByTestId('open-override')).not.toBeVisible();

  // Client mirror of the backend rule: no reopen without a reason.
  await page.getByTestId('open-reopen').click();
  await expect(page.getByTestId('reopen-modal')).toBeVisible();
  await expect(page.getByTestId('reopen-submit')).toBeDisabled();
  await page.fill(
    '[data-testid="reopen-reason"]',
    'The policyholder submitted a second repair estimate after the decision was recorded.'
  );
  await expect(page.getByTestId('reopen-submit')).toBeEnabled();
  await page.getByTestId('reopen-submit').click();
  await expect(page.getByTestId('reopen-modal')).not.toBeVisible({ timeout: 30_000 });

  // Reopened is a review state again: the record shows, the override action
  // returns, and no pipeline re-run happened (the queue row never left).
  await expect(page.getByTestId('reopen-record')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('open-override')).toBeVisible();
  await expect(page.getByTestId('open-reopen')).not.toBeVisible();
  await saveEvidence(page, 'tc-14-reopen-workbench');

  // Persisted: status reopened with a second audit entry, still no run.
  const { token } = await apiLogin(request, adjuster);
  const claim = await request
    .get(`/api/claims/${claimId}`, { headers: authHeaders(token) })
    .then((r) => r.json());
  expect(claim.status, 'reopen flips the status to reopened').toBe('reopened');
  const audit = await request
    .get(`/api/workbench/claims/${claimId}/audit`, { headers: authHeaders(token) })
    .then((r) => r.json());
  const entries = Array.isArray(audit) ? audit : audit.entries ?? [];
  expect(entries.length, 'override + reopen audit entries').toBe(2);
  expect(entries[0].action, 'newest entry is the reopen').toBe('reopen');

  // The explicit second decision — nothing re-adjudicates on its own.
  await page.getByTestId('open-override').click();
  await expect(page.getByTestId('override-modal')).toBeVisible();
  await page.check('input[name="override-decision"][value="rejected"]');
  await page.fill(
    '[data-testid="override-reason"]',
    'Second review confirms the loss is not covered; the earlier approval is superseded.'
  );
  await page.getByTestId('override-submit').click();
  await expect(page.getByTestId('override-modal')).not.toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('override-record')).toBeVisible({ timeout: 30_000 });

  // Customer portal: the chip says the review happened, the second decision
  // replaced the decision section, and the reopened milestone stays on the
  // timeline — without exposing the adjuster's internal reason.
  await page.goto('/status');
  await expect(page.getByTestId('status-portal')).toBeVisible();
  await page.fill('[data-testid="status-claim-input"]', claimId);
  await page.fill('[data-testid="status-code-input"]', claimAccessCode);
  await page.getByTestId('status-lookup-submit').click();
  await expect(page.getByTestId('status-timeline')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('status-badge')).toContainText('Decision updated');
  await expect(page.getByTestId('decision-outcome')).toContainText('rejected');
  await expect(page.getByTestId('milestone-reopened')).toBeVisible();
  await expect(page.getByTestId('status-note')).toHaveCount(0);
  await saveEvidence(page, 'tc-14-reopen-portal');
});
