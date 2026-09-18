import { expect } from '@playwright/test';

// E2E helpers: invite-gated registration through the proxied API, then UI
// login through the real login gate. Each run gets a unique user so the
// serial tests share a clean session.

// The stack must be booted with INVITE_CODE matching this value (compose:
// INVITE_CODE env pass-through; CI sets the same). Overridable per run.
export const E2E_INVITE = process.env.E2E_INVITE_CODE || 'fnol-e2e-invite';

export function uniqueEmail() {
  return `e2e-${Date.now()}-${Math.floor(Math.random() * 1000)}@claimos.dev`;
}

export async function registerUser(request, { role = 'adjuster' } = {}) {
  const email = uniqueEmail();
  const password = 'e2e-Passw0rd!42';
  const res = await request.post('/api/auth/register', {
    data: { email, password, role, inviteCode: E2E_INVITE },
  });
  if (res.status() !== 201) {
    throw new Error(`register failed: ${res.status()} ${await res.text()}`);
  }
  return { email, password };
}

export async function uiLogin(page, { email, password }) {
  await page.goto('/');
  await expect(page.getByTestId('login-gate')).toBeVisible();
  await page.fill('#login-email', email);
  await page.fill('#login-password', password);
  await page.getByRole('button', { name: /sign in/i }).click();
  // The post-login landing view differs by role (adjusters land on the
  // operations dashboard; customers land on their claims view) — the sidebar
  // is the one shell element both roles always render.
  await expect(page.getByTestId('sidebar')).toBeVisible();
}

// Minimal one-page PDF the backend's magic-byte allowlist accepts. Pass a
// label to make uploads byte-unique — each upload appends a new document
// (no de-duplication on the merged #31 route), and distinct fixtures keep
// assertions independent of upload ordering.
export function tinyPdf(label = 'default') {
  return Buffer.from(
    '%PDF-1.4\n' +
      `% document: ${label}\n` +
      '1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n' +
      '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n' +
      '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n' +
      'trailer<</Size 4/Root 1 0 R>>\n' +
      '%%EOF\n',
    'utf8'
  );
}

// ---- API-first helpers (fixture-mode pipeline runs finish in seconds, but
// some scenarios skip the wizard and drive claims through the REST surface).

export function authHeaders(token) {
  return { Authorization: `Bearer ${token}` };
}

export async function apiLogin(request, { email, password }) {
  const res = await request.post('/api/auth/login', { data: { email, password } });
  if (!res.ok()) throw new Error(`login failed: ${res.status()} ${await res.text()}`);
  const body = await res.json();
  return { token: body.accessToken, user: body.user };
}

// Seeded AUTO policies, one constant per deductible so payout assertions
// stay traceable to the policy a spec chose. The eligibility stage adds +25
// risk ("3+ claims on this policy in the past 12 months") once a policy
// carries 3 claims in a year, counting the claim under evaluation — the
// suite submits ~7 pipeline claims, so approval-asserting specs must spread
// across policies. Assignment (files run alphabetically: agent-failure,
// fnol, override, refusal-fallback, sse-fallback, status-portal,
// stp-approval; agent-failure and refusal-fallback are CI-only):
//
//   AUTO-2024-001847 ($500 ded): agent-failure, stp-approval   -> stp is 2nd, clean
//   AUTO-2024-008899 ($600 ded): fnol, refusal-fallback, sse   -> refusal 2nd, clean;
//                                                                  sse is 3rd but only asserts completion
//   AUTO-2024-012001 ($400 ded): override, status-portal       -> portal 2nd, clean
export const POLICY_500_DEDUCTIBLE = 'AUTO-2024-001847'; // $50k limit, theft/accident/vandalism/weather
export const POLICY_600_DEDUCTIBLE = 'AUTO-2024-008899'; // $45k limit, adds hit_and_run
export const POLICY_400_DEDUCTIBLE = 'AUTO-2024-012001'; // $60k limit, adds total_loss

export function theftClaim(over = {}) {
  return {
    policyNumber: POLICY_500_DEDUCTIBLE,
    holderName: 'Dana Whitfield',
    // Fixed past date: incident fingerprints are (policy, date, type), so a
    // per-run "today" would collide with anything reusing the builder on the
    // same day. Specs override incidentDate when they need distinct claims.
    incidentDate: '2026-09-10',
    incidentType: 'theft',
    claimedAmount: 2000,
    description:
      'Bicycle stolen overnight from the apartment bike room; police report case 26-44112 filed the same morning.',
    contactEmail: 'dana.whitfield@example.com',
    documentText: '',
    ...over,
  };
}

export function accidentClaim(over = {}) {
  return theftClaim({
    incidentType: 'accident',
    claimedAmount: 4200,
    description:
      'Rear-end collision on the interstate during the evening commute; bumper and trunk damage; towed to the nearest inspection station.',
    ...over,
  });
}

export async function submitClaim(request, token, payload) {
  const res = await request.post('/api/claims', {
    data: payload,
    headers: authHeaders(token),
  });
  if (!res.ok()) throw new Error(`submit failed: ${res.status()} ${await res.text()}`);
  return res.json(); // {claimId, message, accessCode}
}

// GET /api/claims/{id} is adjuster-only, so terminal-state polling needs an
// adjuster token even when the claim was submitted by a customer. "escalated"
// is terminal for the RUN (gate refused straight-through) and is what the
// workbench override flow starts from.
const TERMINAL_STATUSES = new Set([
  'auto_approved',
  'escalated',
  'overridden',
  'rejected',
  'failed',
]);

export async function waitForTerminal(request, adjusterToken, claimId, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await request.get(`/api/claims/${claimId}`, {
      headers: authHeaders(adjusterToken),
    });
    if (res.ok()) {
      const claim = await res.json();
      if (TERMINAL_STATUSES.has(claim.status)) return claim;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`claim ${claimId} did not reach a terminal status within ${timeoutMs}ms`);
}

// QA evidence: full-page capture goes to E2E_EVIDENCE_DIR when set (the
// evidence sweep enables it; plain runs skip file writes entirely).
export async function saveEvidence(page, name) {
  const dir = process.env.E2E_EVIDENCE_DIR;
  if (!dir) return;
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}
