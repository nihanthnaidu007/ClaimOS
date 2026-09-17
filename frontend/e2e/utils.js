import { expect } from '@playwright/test';

// E2E helpers: invite-gated registration through the proxied API, then UI
// login through the real login gate. Each run gets a unique user so the
// serial tests share a clean session.

export const E2E_INVITE = 'fnol-e2e-invite';

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
  await expect(page.getByTestId('dashboard')).toBeVisible();
}

// Minimal one-page PDF the backend's magic-byte allowlist accepts. Pass a
// label to make uploads byte-unique — the backend de-duplicates identical
// SHA-256 hashes, so distinct documents must not share content.
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
