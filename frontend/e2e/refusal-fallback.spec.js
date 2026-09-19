import { test, expect } from '@playwright/test';
import {
  apiLogin,
  authHeaders,
  registerUser,
  saveEvidence,
  seededAdjuster,
  submitClaim,
  theftClaim,
  waitForTerminal,
  uiLogin,
  POLICY_600_DEDUCTIBLE,
} from './utils';

// Refusal recovery (PR #27): the decision agent retries a refused LLM call
// once; a second refusal falls back to the deterministic template letter so
// the claim still ends with a customer artifact. The template's 0.0
// confidence makes the STP gate escalate instead of auto-finalizing — the
// fallback can never approve a claim by itself.
//
// Two modes, selected by the backend's FIXTURE_FAULT (one stack boot each):
//   refusal:decision        persistent refusal -> template letter -> escalated
//   refusal_once:decision   first-call refusal -> retry rescues -> auto_approved

const MODE = process.env.FIXTURE_FAULT || '';
const PERSISTENT = MODE === 'refusal:decision';

test.skip(
  MODE !== 'refusal:decision' && MODE !== 'refusal_once:decision',
  'run against a stack booted with FIXTURE_FAULT=refusal:decision or refusal_once:decision'
);

let adjuster;
let customer;
let claimId = null;
let adjToken = null;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  adjuster = seededAdjuster();
  customer = await registerUser(request);

  const custToken = (await apiLogin(request, customer)).token;
  const submitted = await submitClaim(
    request,
    custToken,
    // 008899: refusal must land as 2nd claim on its policy — the eligibility
    // frequency rule (+25 risk at 3+/12mo, self included) would tip the
    // rescued run out of auto_approved on a busier slate (see utils.js).
    theftClaim({ incidentDate: '2026-09-08', policyNumber: POLICY_600_DEDUCTIBLE })
  );
  claimId = submitted.claimId;

  // One adjuster login for the whole spec: the limiter counts every POST
  // /api/auth/login from the compose client IP, and this spec runs LAST in
  // the E2E job — the final uiLogin below must fit the same rolling window.
  adjToken = (await apiLogin(request, adjuster)).token;
  await waitForTerminal(request, adjToken, claimId);
});

test('a refused decision call never leaves the claim without an artifact', async ({
  page,
  request,
}) => {
  const claim = await request
    .get(`/api/claims/${claimId}`, { headers: authHeaders(adjToken) })
    .then((r) => r.json());

  if (PERSISTENT) {
    // Second refusal -> template letter (code-owned), 0.0 confidence ->
    // the STP gate escalates. The run completes; it does NOT fail.
    expect(claim.status).toBe('escalated');
  } else {
    // One refusal, one retry: the fixture output stands, confidence stays
    // above the gate, straight-through finalization proceeds.
    expect(claim.status).toBe('auto_approved');
  }

  // The decision record carries a customer letter either way.
  const letter = claim.agent_trace?.decision?.letterBody;
  expect(letter, 'decision letter recorded').toBeTruthy();
  expect(letter).toContain('Dear');

  // The adjuster surface renders the letter body — behind a collapsed
  // disclosure: "View decision letter" triggers the lazy fetch, and only the
  // open section renders letter-body.
  await uiLogin(page, adjuster);
  await page.goto(`/workbench/claims/${claimId}`);
  await expect(page.getByTestId('case-view')).toBeVisible();
  await page.getByTestId('letter-open').click();
  await expect(page.getByTestId('letter-body')).toContainText('Dear', { timeout: 30_000 });
  await saveEvidence(page, PERSISTENT ? 'tc-6-template-letter-fallback' : 'tc-6-refusal-retry');
});
