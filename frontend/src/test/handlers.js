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

export const handlers = [
  http.get(`${API_BASE}/dashboard/stats`, () => HttpResponse.json(dashboardStats)),
];

// One server instance shared by every suite; tests override behavior via
// server.use(...) and setup.js resets handlers between tests.
export const server = setupServer(...handlers);
