// Per-step FNOL validation. Pure: (draft, options) → { field: message }.
// The wizard blocks Next until a step's map is empty; Review re-runs all.

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Canonical lookup verdict, derived from the usePolicyLookup hook result.
// Single source of truth for the inline chip (PolicyLookupField) and the
// validation gate (validateHolder) — the backend answers 404 for unknown
// numbers and PolicyRecord carries snake_case fields with a string status.
export function lookupVerdict(lookup) {
  if (!lookup || (!lookup.isPending && !lookup.isError && !lookup.notFound && !lookup.data)) return 'idle';
  if (lookup.notFound) return 'notfound';
  if (lookup.isPending) return 'pending';
  if (lookup.isError) return 'error';
  if (!lookup.data || lookup.data.status !== 'active') return 'inactive';
  return 'found';
}

export function validateIncident(draft) {
  const errors = {};
  if (!draft.incidentType) errors.incidentType = 'Select the incident type';
  if (!draft.incidentDate) {
    errors.incidentDate = 'Incident date is required';
  } else if (new Date(draft.incidentDate) > new Date()) {
    errors.incidentDate = 'Incident date cannot be in the future';
  }
  if (!draft.description || draft.description.trim().length < 20) {
    errors.description = 'Describe what happened (at least 20 characters)';
  }
  const cost = Number(draft.estimatedCost);
  if (!draft.estimatedCost) {
    errors.estimatedCost = 'Estimated amount is required';
  } else if (Number.isNaN(cost) || cost <= 0) {
    errors.estimatedCost = 'Enter a positive amount';
  } else if (cost > 10_000_000) {
    errors.estimatedCost = 'Amount exceeds the maximum claimable ($10,000,000)';
  }
  return errors;
}

export function validateHolder(draft, lookup) {
  const errors = {};
  const num = (draft.policyNumber || '').trim();
  if (!num) {
    errors.policyNumber = 'Policy number is required';
  } else {
    const verdict = lookupVerdict(lookup);
    if (verdict === 'notfound') {
      errors.policyNumber = `No policy found for ${num}`;
    } else if (verdict === 'inactive') {
      errors.policyNumber = 'This policy is not active';
    }
  }
  if (!draft.holderName || draft.holderName.trim().length < 2) {
    errors.holderName = 'Full name is required';
  }
  if (!draft.holderEmail || !EMAIL_RE.test(draft.holderEmail.trim())) {
    errors.holderEmail = 'A valid email is required';
  }
  if (!draft.incidentRole) errors.incidentRole = 'Select your role in the incident';
  return errors;
}

export const STEP_VALIDATORS = [validateIncident, validateHolder, () => ({})];

// Which step a field belongs to — draft resume scrolls to the first invalid one.
export function firstInvalidStep(draft, lookup) {
  const perStep = STEP_VALIDATORS.map((fn) => fn(draft, lookup));
  const idx = perStep.findIndex((errors) => Object.keys(errors).length > 0);
  return idx === -1 ? null : idx;
}
