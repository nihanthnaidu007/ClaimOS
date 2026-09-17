// MSW v2 request handlers.
//
// Components build their API URLs as `${import.meta.env.VITE_API_BASE_URL}/api`
// (Dashboard.js and friends). vite.config.js pins `test.env.VITE_API_BASE_URL`
// so this module derives handler paths from the same base URL the components
// resolve at runtime — one source of truth for the API contract.
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;

export const dashboardStats = {
  totalClaims: 12,
  approved: 5,
  rejected: 3,
  underReview: 2,
  pending: 2,
  totalPayout: 48250.75,
  avgRiskScore: 41,
  activePolicies: 8,
  recentClaims: [
    {
      id: 'CLM-0001',
      holder_name: 'Ada Lovelace',
      policy_number: 'POL-99',
      incident_type: 'collision',
      claimed_amount: 4200,
      status: 'approved',
    },
  ],
};

export const sessionUser = {
  id: 'usr_test1',
  email: 'adjuster@claimos.dev',
  role: 'adjuster',
  createdAt: '2026-09-01T00:00:00Z',
};

export const workbenchQueueRows = [
  {
    id: 'CLM-1001',
    policy_number: 'POL-77',
    holder_name: 'Grace Hopper',
    incident_type: 'theft',
    claimed_amount: 18500,
    status: 'escalated',
    risk_score: 0.82,
    created_at: '2026-09-15T08:00:00+00:00',
    severity: 'elevated',
    sla: { targetHours: 24, hoursElapsed: 50, hoursRemaining: -26, breached: true, state: 'breached' },
  },
  {
    id: 'CLM-1002',
    policy_number: 'POL-12',
    holder_name: 'Alan Turing',
    incident_type: 'windshield',
    claimed_amount: 320,
    status: 'pending',
    risk_score: 0.11,
    created_at: '2026-09-17T06:00:00+00:00',
    severity: 'low',
    sla: { targetHours: 72, hoursElapsed: 2, hoursRemaining: 70, breached: false, state: 'ok' },
  },
];

export const workbenchCaseSummary = {
  claimId: 'CLM-1001',
  holderName: 'Grace Hopper',
  policyNumber: 'POL-77',
  incidentType: 'theft',
  incidentDate: '2026-09-14',
  claimedAmount: 18500,
  status: 'escalated',
  severity: 'elevated',
  riskScore: 0.82,
  recommendation: 'escalate',
  confidence: 0.63,
  eligibility: { eligible: false, riskFactors: ['high value'], fraudIndicators: [] },
  coverage: { found: true, statusCheck: 'active', withinLimits: true, adjustedPayout: 18000 },
  documents: { consistencyScore: 0.94, redFlags: [] },
  decision: { verdict: null, payoutAmount: null, letterSubject: null, hasLetterBody: false },
  intakeValid: true,
  stages: [
    { agent: 'intake', label: 'Intake', reached: true, status: 'complete', durationMs: 120, reasoning: 'normalized' },
    { agent: 'policy', label: 'Policy verification', reached: true, status: 'complete', durationMs: 80, reasoning: null },
    { agent: 'documents', label: 'Document analysis', reached: true, status: 'complete', durationMs: 95, reasoning: null },
    { agent: 'eligibility', label: 'Eligibility & risk', reached: true, status: 'complete', durationMs: 140, reasoning: null },
    { agent: 'decision', label: 'Decision', reached: false, status: 'pending', durationMs: null, reasoning: null },
  ],
  sla: { targetHours: 24, hoursElapsed: 50, hoursRemaining: -26, breached: true, state: 'breached' },
  escalationReason: 'high value claim',
  failureReason: null,
  override: null,
  source: 'stored agent traces',
};

export const opsAnalytics = {
  cycleTime: { p50Seconds: 54000, p95Seconds: 129600, decided: 18 },
  stp: { decided: 18, autoApproved: 11, escalated: 7, rate: 0.6111111111111112 },
  fraud: { totalClaims: 27, flaggedClaims: 4, rate: 0.14814814814814814 },
  decisions: [
    { status: 'auto_approved', count: 11 },
    { status: 'escalated', count: 7 },
    { status: 'pending', count: 6 },
    { status: 'rejected', count: 3 },
  ],
  sla: {
    bySeverity: [
      { severity: 'elevated', slaHours: 24, decided: 7, breaches: 2, breachRate: 0.2857142857142857 },
      { severity: 'low', slaHours: 48, decided: 11, breaches: 0, breachRate: 0 },
    ],
  },
};

export const handlers = [
  // Session restore: every AuthProvider mount probes this endpoint.
  http.post(`${API_BASE}/auth/refresh`, () =>
    HttpResponse.json({
      accessToken: 'test-access-token',
      tokenType: 'bearer',
      expiresInSeconds: 900,
      user: sessionUser,
    })
  ),
  http.get(`${API_BASE}/dashboard/stats`, () => HttpResponse.json(dashboardStats)),
  http.get(`${API_BASE}/claims`, () => HttpResponse.json(dashboardStats.recentClaims)),
  http.get(`${API_BASE}/workbench/queue`, () =>
    HttpResponse.json({ rows: workbenchQueueRows, generatedAt: '2026-09-17T10:00:00+00:00' })
  ),
  http.get(`${API_BASE}/analytics/ops`, () => HttpResponse.json(opsAnalytics)),
  http.get(`${API_BASE}/claims/:claimId/documents`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/claims/:claimId/documents`, () =>
    HttpResponse.json(
      {
        id: 'DOC-NEW-1',
        claim_id: 'CLM-1001',
        file_name: 'accident-report.pdf',
        content_type: 'application/pdf',
        size_bytes: 324,
        storage_key: 'claims/CLM-1001/doc-1',
        uploaded_at: '2026-09-17T10:05:00+00:00',
        uploaded_by: 'ops-demo@claimos.dev',
        sha256: 'abc123',
      },
      { status: 201 }
    )
  ),
];

// One server instance shared by every suite; tests override behavior via
// server.use(...) and setup.js resets handlers between tests.
export const server = setupServer(...handlers);
