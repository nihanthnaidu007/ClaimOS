// FNOL validation tests — pure validators plus the shared lookupVerdict
// mapping. The not-found/inactive cases are regression tests: the validation
// gate previously read a status string the lookup hook never produced, so
// dead policy numbers passed every step.
import { describe, it, expect } from 'vitest';
import { validateIncident, validateHolder, lookupVerdict, firstInvalidStep } from './validation';

const VALID_INCIDENT = {
  incidentType: 'accident',
  incidentDate: '2026-09-01',
  description: 'A driver rear-ended my parked car this morning.',
  estimatedCost: '1200',
};

const HOOK_FOUND = { isPending: false, isError: false, notFound: false, data: { status: 'active' } };

describe('validateIncident', () => {
  it('accepts a complete incident step', () => {
    expect(validateIncident(VALID_INCIDENT)).toEqual({});
  });

  it('requires every incident field', () => {
    const errors = validateIncident({});
    expect(errors.incidentType).toBe('Select the incident type');
    expect(errors.incidentDate).toBe('Incident date is required');
    expect(errors.description).toBeDefined();
    expect(errors.estimatedCost).toBe('Estimated amount is required');
  });

  it('rejects a future incident date', () => {
    const future = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const errors = validateIncident({ ...VALID_INCIDENT, incidentDate: future });
    expect(errors.incidentDate).toBe('Incident date cannot be in the future');
  });

  it('rejects descriptions under 20 characters', () => {
    const errors = validateIncident({ ...VALID_INCIDENT, description: 'too short' });
    expect(errors.description).toBe('Describe what happened (at least 20 characters)');
  });

  it('rejects zero, negative, and absurd amounts', () => {
    expect(validateIncident({ ...VALID_INCIDENT, estimatedCost: '0' }).estimatedCost).toBe('Enter a positive amount');
    expect(validateIncident({ ...VALID_INCIDENT, estimatedCost: '-5' }).estimatedCost).toBe('Enter a positive amount');
    expect(validateIncident({ ...VALID_INCIDENT, estimatedCost: '10000001' }).estimatedCost).toContain('maximum claimable');
  });
});

describe('lookupVerdict', () => {
  it('maps an idle/absent lookup to idle', () => {
    expect(lookupVerdict(null)).toBe('idle');
    expect(lookupVerdict({ isPending: false, isError: false, notFound: false, data: undefined })).toBe('idle');
  });

  it('maps hook states to verdicts', () => {
    expect(lookupVerdict({ isPending: true, isError: false, notFound: false, data: undefined })).toBe('pending');
    expect(lookupVerdict({ isPending: false, isError: true, notFound: true, data: undefined })).toBe('notfound');
    expect(lookupVerdict({ isPending: false, isError: true, notFound: false, data: undefined })).toBe('error');
  });

  it('maps PolicyRecord.status to found/inactive', () => {
    expect(lookupVerdict(HOOK_FOUND)).toBe('found');
    expect(lookupVerdict({ ...HOOK_FOUND, data: { status: 'inactive' } })).toBe('inactive');
    expect(lookupVerdict({ ...HOOK_FOUND, data: { status: 'expired' } })).toBe('inactive');
  });
});

describe('validateHolder', () => {
  const VALID_HOLDER = {
    policyNumber: 'POL-2024-001847',
    holderName: 'Sarah Chen',
    holderEmail: 'sarah@example.com',
    incidentRole: 'policyholder',
  };

  it('accepts a complete holder step against an active policy', () => {
    expect(validateHolder(VALID_HOLDER, HOOK_FOUND)).toEqual({});
  });

  it('requires the policy number before any lookup runs', () => {
    const errors = validateHolder({ ...VALID_HOLDER, policyNumber: '' }, null);
    expect(errors.policyNumber).toBe('Policy number is required');
  });

  it('blocks a not-found policy number (regression: verdict shape)', () => {
    const notFound = { isPending: false, isError: true, notFound: true, data: undefined };
    const errors = validateHolder(VALID_HOLDER, notFound);
    expect(errors.policyNumber).toBe('No policy found for POL-2024-001847');
  });

  it('blocks an inactive policy number (regression: verdict shape)', () => {
    const inactive = { ...HOOK_FOUND, data: { status: 'inactive' } };
    const errors = validateHolder(VALID_HOLDER, inactive);
    expect(errors.policyNumber).toBe('This policy is not active');
  });

  it('does not block while the lookup is still pending', () => {
    const pending = { isPending: true, isError: false, notFound: false, data: undefined };
    const errors = validateHolder(VALID_HOLDER, pending);
    expect(errors.policyNumber).toBeUndefined();
  });

  it('requires holder identity fields', () => {
    const errors = validateHolder(
      { policyNumber: 'POL-1', holderName: 'A', holderEmail: 'nope', incidentRole: '' },
      null
    );
    expect(errors.holderName).toBe('Full name is required');
    expect(errors.holderEmail).toBe('A valid email is required');
    expect(errors.incidentRole).toBe('Select your role in the incident');
  });
});

describe('firstInvalidStep', () => {
  it('maps the first blocking field to its step', () => {
    expect(firstInvalidStep({}, null)).toBe(0);
    expect(firstInvalidStep(VALID_INCIDENT, null)).toBe(1);
    expect(
      firstInvalidStep(
        { ...VALID_INCIDENT, policyNumber: 'POL-1', holderName: 'Sarah Chen', holderEmail: 'sarah@example.com', incidentRole: 'policyholder' },
        HOOK_FOUND
      )
    ).toBeNull();
  });
});
