import { test, expect } from '@playwright/test';
import { saveEvidence, seededAdjuster, uiLogin, tinyPdf, POLICY_600_DEDUCTIBLE } from './utils';

// Serial: the upload spec reuses the claim submitted by the happy-path spec.
test.describe.configure({ mode: 'serial' });

// 008899 ($600 deductible): the frequency rule escalates policies carrying
// 3+ claims/12mo (see utils.js) — fnol shares it with refusal-fallback/sse
// and asserts decision persistence, not a verdict, so the slate stays clean
// for refusal's auto-approval assertions.
const POLICY_NUMBER = POLICY_600_DEDUCTIBLE;
const DESCRIPTION =
  'Rear-ended at a stop light on Route 9; bumper damage and the trunk will not close.';

let user;
let submittedClaimId = null;

test.beforeAll(async () => {
  user = seededAdjuster();
});

test('wizard blocks invalid input, resumes drafts, and reports policy misses', async ({
  page,
}) => {
  await uiLogin(page, user);
  await page.goto('/new-claim');

  // 1. Empty submission: inline errors, no step change.
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-0')).toBeVisible();
  await expect(page.locator('[data-testid="wizard-step-0"] [class*="ef4444"]').first()).toBeVisible();

  // 2. Fill the incident step; drafts autosave locally + server-side.
  await page.selectOption('[data-testid="incident-type"]', 'accident');
  await page.fill('[data-testid="incident-date"]', '2026-09-15');
  await page.fill('[data-testid="estimated-cost"]', '4200');
  await page.fill('[data-testid="incident-description"]', DESCRIPTION);
  await page
    .waitForTimeout(1200); // debounce + localStorage/server draft write

  // 3. Reload: the draft resumes and the banner explains where it came from.
  await page.reload();
  await expect(page.getByTestId('draft-resume-banner')).toBeVisible();
  await expect(page.locator('[data-testid="incident-type"]')).toHaveValue('accident');
  await expect(page.locator('[data-testid="incident-description"]')).toHaveValue(DESCRIPTION);
  await page.getByTestId('draft-dismiss-btn').click();

  // 4. On to the holder step: an unknown policy number reports not-found inline.
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-1')).toBeVisible();
  await page.fill('[data-testid="policy-number-input"]', 'POL-NOPE-404');
  await expect(page.getByTestId('policy-number-feedback')).toContainText(/not found|no policy/i, {
    timeout: 20_000,
  });
  // The step stays blocked without a live lookup verdict.
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-1')).toBeVisible();
});

test('submission runs the live pipeline to a persisted decision', async ({ page }) => {
  await uiLogin(page, user);
  await page.goto('/new-claim');

  // Incident
  await page.selectOption('[data-testid="incident-type"]', 'accident');
  await page.fill('[data-testid="incident-date"]', '2026-09-15');
  await page.fill('[data-testid="estimated-cost"]', '4200');
  await page.fill('[data-testid="incident-description"]', DESCRIPTION);
  await page
    .setInputFiles('[data-testid="documents-input"]', {
      name: 'police-report.pdf',
      mimeType: 'application/pdf',
      buffer: tinyPdf('wizard-police-report'),
    });

  // Policy holder (with a real seeded policy so the lookup returns active)
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-1')).toBeVisible();
  await page.fill('[data-testid="holder-name"]', 'Dana Whitfield');
  await page.fill('[data-testid="holder-email"]', 'dana.whitfield@example.com');
  await page.selectOption('[data-testid="incident-role"]', 'policyholder');
  await page.fill('[data-testid="policy-number-input"]', POLICY_NUMBER);
  await expect(page.getByTestId('policy-found-panel')).toBeVisible({ timeout: 20_000 });

  // Coverage + review, then submit; capture the claim id from the POST.
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-2')).toBeVisible();
  await page.getByTestId('wizard-next-btn').click();
  await expect(page.getByTestId('wizard-step-3')).toBeVisible();

  const claimCreated = page
    .waitForResponse(
      (r) => r.url().includes('/api/claims') && r.request().method() === 'POST'
    )
    .then((r) => r.json());
  await page.getByTestId('submit-claim-btn').click();
  const claim = await claimCreated;
  submittedClaimId = claim.id || claim._id || claim.claimId;
  expect(submittedClaimId).toBeTruthy();

  // Live pipeline board over SSE with a live connection chip.
  await expect(page.getByTestId('pipeline-board')).toBeVisible();
  await expect(page.getByTestId('connection-chip')).toBeVisible();

  // Real agents run to completion; the persisted decision replaces the board.
  // Live latency: main's tuned prompts miss the typed schemas on the first
  // pass often enough that stages burn 2-3 LLM round-trips (the adapter's
  // bounded schema retry rescues them — see adapter.py), so a full run can
  // legitimately take 5+ minutes. 480s covers it without masking hangs.
  await expect(page.getByTestId('decision-panel')).toBeVisible({ timeout: 480_000 });
  // The pipeline must reach a REAL decision — the failure banner renders the
  // same panel with a "PROCESSING FAILED" verdict and would satisfy the
  // visibility assertion above.
  await expect(page.getByTestId('decision-panel')).not.toContainText('PROCESSING FAILED');
  await saveEvidence(page, 'tc-1-fnol-decision-panel');
});

test('claim detail accepts uploads and streams the evidence pack', async ({ page }) => {
  expect(submittedClaimId, 'happy path must have submitted a claim').toBeTruthy();

  await uiLogin(page, user);
  await page.goto(`/claims/${submittedClaimId}`);
  await expect(page.getByTestId('claim-detail')).toBeVisible();

  // Full per-agent trace timeline from durable logs.
  await expect(page.getByTestId('trace-timeline')).toBeVisible();
  await expect(page.getByTestId('trace-confidence').first()).toBeVisible();
  // The persisted claim state is terminal — no pending pill.
  await expect(page.getByTestId('claim-status-pill')).not.toContainText(/pending/i);

  // Upload a supporting PDF on the claim detail surface. BOTH waits are armed
  // before setInputFiles: the API answers the upload in ~15ms, so a wait armed
  // after the interaction starts can miss the entire exchange (the POST fires
  // from the change handler and completes before the next Playwright command
  // dispatches) and starve for its full timeout. The 201 itself is asserted
  // through the persisted row: the client refetches only on a 2xx upload, so
  // row count +1 IS the accepted outcome.
  const rowsBefore = await page.getByTestId('document-row').count();
  const refetchGet = page.waitForResponse(
    (r) => r.url().includes('/documents') && r.request().method() === 'GET',
    { timeout: 30_000 }
  );
  const uploadPost = page.waitForRequest(
    (r) => r.url().includes('/documents') && r.method() === 'POST',
    { timeout: 30_000 }
  );
  await page
    .setInputFiles('[data-testid="document-upload-input"]', {
      name: 'repair-estimate.pdf',
      mimeType: 'application/pdf',
      buffer: tinyPdf('detail-repair-estimate'),
    });
  await uploadPost;
  await expect(page.getByTestId('document-row')).toHaveCount(rowsBefore + 1, {
    timeout: 30_000,
  });
  const refetch = await refetchGet;
  expect(refetch.status(), 'documents refetch must return 200').toBe(200);

  // Evidence pack: the API returns a JSON envelope whose base64 payload
  // decodes to a real PDF (the client turns it into a Blob download).
  const packResponse = page.waitForResponse((r) =>
    r.url().includes('/evidence-pack')
  );
  await page.getByTestId('evidence-pack-btn').click();
  const res = await packResponse;
  expect(res.status()).toBe(200);
  expect(res.headers()['content-type']).toContain('application/json');
  const pack = await res.json();
  expect(pack.filename).toMatch(/evidence-pack-.*\.pdf$/);
  const pdfBytes = Buffer.from(pack.pdf, 'base64');
  expect(pdfBytes.subarray(0, 5).toString('utf8')).toBe('%PDF-');
  await saveEvidence(page, 'tc-1-fnol-evidence-pack');
});
