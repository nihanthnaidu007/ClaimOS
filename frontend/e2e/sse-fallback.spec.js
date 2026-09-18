import { test, expect } from '@playwright/test';
import { registerUser, saveEvidence, uiLogin } from './utils';

// Polling fallback: with the SSE endpoint unreachable from the browser, the
// pipeline board must still reach a decision — the SSE hook's reconnect
// cycle invalidates the claim queries on every attempt, so server truth
// arrives without the stream — and the connection chip must report the
// degraded state instead of pretending to be live.

const POLICY_NUMBER = 'AUTO-2024-001847';
const DESCRIPTION =
  'Bicycle stolen overnight from the apartment bike room; police report case 26-44112 filed the same morning.';

let user;

test.beforeAll(async ({ request }) => {
  user = await registerUser(request, { role: 'customer' });
});

test('the board finalizes through polling when the event stream is blocked', async ({
  page,
}) => {
  // Abort the durable event stream for this page only — the REST surface
  // stays untouched, so this isolates the SSE failure mode.
  await page.route('**/api/events/streams/**', (route) => route.abort());

  await uiLogin(page, user);
  await page.goto('/new-claim');

  await page.selectOption('[data-testid="incident-type"]', 'theft');
  await page.fill('[data-testid="incident-date"]', '2026-09-11');
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
  await page.getByTestId('submit-claim-btn').click();

  await expect(page.getByTestId('pipeline-board')).toBeVisible();

  // The stream is dead: the chip may cycle CONNECTING/RECONNECTING and must
  // end OFFLINE after the bounded retry budget — never LIVE.
  await expect(page.getByTestId('connection-chip')).not.toContainText('LIVE', {
    timeout: 30_000,
  });

  // The run still completes: poll-driven refetches carry the decision and
  // the persisted panel replaces the board.
  await expect(page.getByTestId('decision-panel')).toBeVisible({ timeout: 180_000 });
  await expect(page.getByTestId('decision-panel')).not.toContainText('PROCESSING FAILED');
  await saveEvidence(page, 'tc-7-sse-polling-fallback');
});
