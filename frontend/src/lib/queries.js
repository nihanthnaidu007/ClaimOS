// TanStack Query hooks — the only place components touch the API.
//
// Server state lives in the query cache; components never fetch in effects.
// Keys are centralized so SSE handlers and mutations can target the same
// cache entries (see usePipelineEvents invalidation).
import { useMutation, useQuery } from '@tanstack/react-query';
import api from './api';

export const queryKeys = {
  dashboard: ['dashboard', 'stats'],
  claims: ['claims'],
  claim: (id) => ['claims', id],
  trace: (id) => ['claims', id, 'trace'],
  documents: (id) => ['claims', id, 'documents'],
  policy: (number) => ['policies', 'lookup', number],
  pipeline: (id) => ['pipeline', id],
};

export function useDashboardStats() {
  return useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get('/dashboard/stats').then((r) => r.data),
  });
}

export function useClaims() {
  return useQuery({
    queryKey: queryKeys.claims,
    queryFn: () => api.get('/claims').then((r) => r.data),
  });
}

export function useClaim(claimId, options = {}) {
  return useQuery({
    queryKey: queryKeys.claim(claimId),
    queryFn: () => api.get(`/claims/${claimId}`).then((r) => r.data),
    enabled: Boolean(claimId),
    ...options,
  });
}

export function useClaimTrace(claimId) {
  return useQuery({
    queryKey: queryKeys.trace(claimId),
    queryFn: () => api.get(`/claims/${claimId}/trace`).then((r) => r.data),
    enabled: Boolean(claimId),
  });
}

export function useClaimDocuments(claimId) {
  return useQuery({
    queryKey: queryKeys.documents(claimId),
    queryFn: () => api.get(`/claims/${claimId}/documents`).then((r) => r.data),
    enabled: Boolean(claimId),
  });
}

// Live policy lookup: the caller passes an already-debounced policy number and
// owns the `enabled` gate (>= 3 chars). 404 is an expected answer ("no such
// policy"), surfaced as `notFound` instead of an error state.
export function usePolicyLookup(policyNumber, { enabled = true } = {}) {
  const query = useQuery({
    queryKey: queryKeys.policy(policyNumber),
    queryFn: () =>
      api
        .get('/policies/lookup', { params: { policy_number: policyNumber } })
        .then((r) => r.data),
    enabled: enabled && Boolean(policyNumber && policyNumber.trim().length >= 3),
    staleTime: 30_000,
    retry: false,
  });
  return { ...query, notFound: query.error?.response?.status === 404 };
}

// Full policy list + typeahead search for the PolicyLookup page. Empty query
// returns the unfiltered list; the 300ms debounce lives in the component.
export function usePolicySearch(query) {
  const trimmed = (query || '').trim();
  return useQuery({
    queryKey: ['policies', 'search', trimmed],
    queryFn: () =>
      api
        .get(trimmed ? '/policies/search' : '/policies', {
          params: trimmed ? { q: trimmed } : undefined,
        })
        .then((r) => r.data),
    staleTime: 30_000,
  });
}

export function useSubmitClaim() {
  return useMutation({
    mutationFn: (submission) => api.post('/claims', submission).then((r) => r.data),
  });
}
