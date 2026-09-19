import { test, expect } from '@playwright/test';
import {
  apiLogin,
  accidentClaim,
  authHeaders,
  registerUser,
  seededAdjuster,
  submitClaim,
  tinyPdf,
  uiLogin,
  waitForTerminal,
  POLICY_500_DEDUCTIBLE,
} from './utils';

// Spec F4: the portal documents card. The adjuster requests a document (F3
// checklist); the customer sees it in the public portal after a claim-number
// + access-code lookup, uploads the file against the request, and the
// adjuster sees the request flip to received with a linked document.
//
// Claim placement: sorts BEFORE status-portal deliberately — that spec
// hammers /api/status/lookup to 429 for its rate-limit test, and the portal
// limit is per-IP, so any spec after it inherits a spent window. Portal-upload
// runs 3rd in the phase-1 alphabetical order as the 1st claim on 001847; the
// frequency rule (3+/12mo) stays quiet and stp-approval's 2nd claim on the
// same policy still auto-approves.

const REQUEST_TITLE = 'Repair estimate from the shop';

let adjuster;
let customer;
let claim = null;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  // Registration is customer-only (the server assigns the role); adjuster
  // sessions come from the seeded demo account.
  adjuster = seededAdjuster();
  customer = await registerUser(request);

  const custToken = (await apiLogin(request, customer)).token;
  // $4,200 accident -> elevated severity -> STP refuses -> escalated (terminal).
  claim = await submitClaim(
    request,
    custToken,
    accidentClaim({ incidentDate: '2026-09-14', policyNumber: POLICY_500_DEDUCTIBLE })
  );

  const adjToken = (await apiLogin(request, adjuster)).token;
  await waitForTerminal(request, adjToken, claim.claimId);

  // The adjuster asks the customer for a document through the F3 checklist.
  const res = await request.post(`/api/claims/${claim.claimId}/document-requests`, {
    data: { title: REQUEST_TITLE, description: 'A signed estimate from the repair shop.' },
    headers: authHeaders(adjToken),
  });
  if (!res.ok()) throw new Error(`document request failed: ${res.status()} ${await res.text()}`);
});

test('customer uploads a requested document and the adjuster sees it received', async ({ page, request }) => {
  // 1. Portal lookup with claim number + access code — the only credential.
  await page.goto('/status');
  await expect(page.getByTestId('status-portal')).toBeVisible();
  await page.fill('[data-testid="status-claim-input"]', claim.claimId);
  await page.fill('[data-testid="status-code-input"]', claim.accessCode);
  await page.getByTestId('status-lookup-submit').click();
  await expect(page.getByTestId('status-timeline')).toBeVisible({ timeout: 20_000 });

  // 2. The documents card lists the adjuster's request as still needed.
  const card = page.getByTestId('portal-documents-card');
  await expect(card).toBeVisible();
  const item = card.getByTestId('docreq-item');
  await expect(item).toHaveAttribute('data-status', 'requested');
  await expect(item.getByText(REQUEST_TITLE)).toBeVisible();

  // 3. Upload a small PDF against the request; the chip flips to received
  //    once the refreshed portal projection comes back.
  await item.getByTestId('docreq-upload-input').setInputFiles({
    name: 'repair-estimate.pdf',
    mimeType: 'application/pdf',
    buffer: tinyPdf('portal-upload'),
  });
  await item.getByTestId('docreq-upload-submit').click();
  await expect(item).toHaveAttribute('data-status', 'received', { timeout: 20_000 });
  await expect(card.getByTestId('docreq-upload-error')).toHaveCount(0);

  // 4. Server state: the request links the uploaded document (adjuster API).
  const adjToken = (await apiLogin(request, adjuster)).token;
  const res = await request.get(`/api/claims/${claim.claimId}/document-requests`, {
    headers: authHeaders(adjToken),
  });
  expect(res.ok()).toBeTruthy();
  const requests = await res.json();
  expect(requests).toHaveLength(1);
  expect(requests[0].status).toBe('received');
  expect(requests[0].document_id).toBeTruthy();

  // 5. The adjuster sees the request as received in the workbench case view.
  await uiLogin(page, adjuster);
  await page.goto('/workbench');
  await expect(page.getByTestId('workbench-queue')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId(`queue-row-${claim.claimId}`).click();
  await expect(page.getByTestId('case-view')).toBeVisible();
  const adjusterItem = page.getByTestId('docreq-item');
  await expect(adjusterItem).toHaveAttribute('data-status', 'received');
  await expect(adjusterItem.getByText(REQUEST_TITLE)).toBeVisible();
});
