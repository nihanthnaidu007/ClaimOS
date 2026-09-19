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

const sessionUser = {
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
    escalated_at: '2026-09-17T09:30:00+00:00',
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
  escalatedAt: '2026-09-17T09:30:00+00:00',
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
  // Workload per adjuster (spec F10): busiest first, unassigned bucket last.
  workload: {
    adjusters: [
      { assigneeId: 'usr_adj_1', email: 'maya.adjuster@claimos.example', openClaims: 4 },
      { assigneeId: 'usr_adj_2', email: 'omar.adjuster@claimos.example', openClaims: 3 },
    ],
    unassigned: 2,
  },
  escalations: { total: 2, unassigned: 1 },
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

  // Document checklists (spec F3): an empty checklist by default; tests
  // override with server.use(...) to script lists, mutations, and failures.
  http.get(`${API_BASE}/claims/:claimId/document-requests`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/claims/:claimId/document-requests`, async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json(
      {
        id: 'dreq_new_1',
        claim_id: 'CLM-1001',
        title: body.title,
        description: body.description || '',
        status: 'requested',
        requested_by: 'usr_test1',
        document_id: null,
        created_at: '2026-09-17T10:10:00+00:00',
        updated_at: '2026-09-17T10:10:00+00:00',
      },
      { status: 201 }
    );
  }),
  http.patch(`${API_BASE}/claims/:claimId/document-requests/:requestId`, async ({ request }) => {
    const body = await request.json();
    const waived = Boolean(body.waive);
    return HttpResponse.json({
      id: 'dreq_new_1',
      claim_id: 'CLM-1001',
      title: body.title || 'Repair estimate',
      description: body.description || '',
      status: waived ? 'waived' : 'requested',
      requested_by: 'usr_test1',
      document_id: null,
      created_at: '2026-09-17T10:10:00+00:00',
      updated_at: '2026-09-17T11:00:00+00:00',
    });
  }),

  // Portal document uploads (spec F4): the default is a 201 receipt; tests
  // override with server.use(...) to script validation failures.
  http.post(`${API_BASE}/status/upload-document`, async ({ request }) => {
    const form = await request.formData();
    const file = form.get('file');
    return HttpResponse.json(
      {
        requestId: form.get('requestId') || 'dreq_portal01',
        status: 'received',
        documentId: 'doc_new_1',
        filename: file ? file.name : 'estimate.pdf',
        sizeBytes: file ? file.size : 0,
      },
      { status: 201 }
    );
  }),

  // FNOL wizard: no server draft by default (404 = local copy is truth);
  // PUT echoes the upsert the useWizardDraft autosave performs.
  http.get(`${API_BASE}/fnol/drafts/:draftId`, () => new HttpResponse(null, { status: 404 })),
  http.put(`${API_BASE}/fnol/drafts/:draftId`, async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json({ data: body.data, updatedAt: new Date().toISOString() });
  }),

  // F5 claim messaging: the case-view thread reads the (empty by default)
  // conversation so CaseView suites render under onUnhandledRequest: 'error'.
  http.get(`${API_BASE}/workbench/claims/:claimId/messages`, () =>
    HttpResponse.json({ claimId: 'CLM-1001', messages: [] })
  ),

  // Policy lookup: an ACTIVE PolicyRecord (snake_case, like the backend).
  http.get(`${API_BASE}/policies/lookup`, ({ request }) => {
    const url = new URL(request.url);
    const num = url.searchParams.get('policy_number') || '';
    if (num.toUpperCase() === 'POL-DEAD') {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json({
      id: 'pol_fixture1',
      policy_number: num || 'POL-2024-001847',
      holder_name: 'Sarah Chen',
      holder_email: 'sarah.chen@example.com',
      holder_phone: '',
      policy_type: 'auto',
      status: 'active',
      coverage_limit: 50000,
      deductible: 500,
      monthly_premium: 120,
    });
  }),

  // F12 saved views + bulk actions: inert defaults; suites override per test.
  http.get(`${API_BASE}/workbench/views`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/workbench/views`, async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json(
      { id: 'vw_saved', name: body.name, filters: body.filters || {}, createdAt: new Date().toISOString() },
      { status: 201 }
    );
  }),
  http.get(`${API_BASE}/workbench/views/:viewId/apply`, () =>
    HttpResponse.json({
      view: { id: 'vw_default', name: 'View', filters: {}, createdAt: new Date().toISOString() },
      rows: workbenchQueueRows,
      generatedAt: new Date().toISOString(),
    })
  ),
  http.delete(`${API_BASE}/workbench/views/:viewId`, () => HttpResponse.json({ deleted: true })),
  http.post(`${API_BASE}/workbench/claims/bulk`, () =>
    HttpResponse.json({ action: 'flag', results: [], updated: 0, failed: 0 })
  ),
];

// One server instance shared by every suite; tests override behavior via
// server.use(...) and setup.js resets handlers between tests.
export const server = setupServer(...handlers);
