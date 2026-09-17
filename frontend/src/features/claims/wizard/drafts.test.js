// Draft-model tests — pure persistence helpers, no React, no network.
// localStorage behaviors are exercised directly through jsdom.
import { describe, it, expect, beforeEach } from 'vitest';
import {
  DRAFT_KEY,
  EMPTY_DRAFT,
  createDraft,
  loadLocalDraft,
  saveLocalDraft,
  clearLocalDraft,
  ensureDraftId,
  newerDraft,
} from './drafts';

const PARTIAL = { policyNumber: 'POL-2024-001847', incidentType: 'accident' };

describe('createDraft', () => {
  it('fills the empty draft defaults around partial data', () => {
    const draft = createDraft(PARTIAL);
    expect(draft.policyNumber).toBe('POL-2024-001847');
    expect(draft.incidentType).toBe('accident');
    expect(draft.holderName).toBe('');
    expect(draft.incidentRole).toBe(EMPTY_DRAFT.incidentRole);
  });

  it('returns a full empty draft for null/undefined input', () => {
    expect(createDraft(null)).toEqual(EMPTY_DRAFT);
  });
});

describe('local draft persistence', () => {
  beforeEach(() => window.localStorage.clear());

  it('returns null when nothing is stored', () => {
    expect(loadLocalDraft()).toBeNull();
  });

  it('returns null for corrupt JSON instead of throwing', () => {
    window.localStorage.setItem(DRAFT_KEY, '{not json');
    expect(loadLocalDraft()).toBeNull();
  });

  it('round-trips through save and load', () => {
    saveLocalDraft(PARTIAL, 2);
    const loaded = loadLocalDraft();
    expect(loaded.policyNumber).toBe('POL-2024-001847');
    expect(loaded.incidentType).toBe('accident');
  });

  it('clear removes the stored copy', () => {
    saveLocalDraft(PARTIAL, 0);
    clearLocalDraft();
    expect(loadLocalDraft()).toBeNull();
  });
});

describe('ensureDraftId', () => {
  beforeEach(() => window.localStorage.clear());

  it('mints a draft_ id when no draft exists', () => {
    const id = ensureDraftId();
    expect(id).toMatch(/^draft_/);
  });

  it('reuses the draft id stored with a saved draft', () => {
    window.localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({ data: PARTIAL, step: null, savedAt: new Date().toISOString(), draftId: 'draft_abc123' })
    );
    expect(ensureDraftId()).toBe('draft_abc123');
  });
});

describe('newerDraft', () => {
  it('prefers the server copy when it is strictly newer', () => {
    const local = { ...PARTIAL, savedAt: '2026-09-17T10:00:00Z' };
    const server = { data: { policyNumber: 'POL-SERVER' }, updatedAt: '2026-09-17T11:00:00Z' };
    expect(newerDraft(server, local).policyNumber).toBe('POL-SERVER');
  });

  it('keeps the local copy when it is newer', () => {
    const local = { ...PARTIAL, savedAt: '2026-09-17T12:00:00Z' };
    const server = { data: { policyNumber: 'POL-SERVER' }, updatedAt: '2026-09-17T11:00:00Z' };
    expect(newerDraft(server, local).policyNumber).toBe('POL-2024-001847');
  });

  it('falls back to the local copy when the server has no data', () => {
    const local = { ...PARTIAL, savedAt: new Date().toISOString() };
    expect(newerDraft(null, local)).toEqual(local);
    expect(newerDraft({}, local)).toEqual(local);
  });

  it('adopts the server copy when no local draft exists', () => {
    const server = { data: { policyNumber: 'POL-SERVER' }, updatedAt: '2026-09-17T11:00:00Z' };
    expect(newerDraft(server, null).policyNumber).toBe('POL-SERVER');
  });

  it('returns null when neither side has a draft', () => {
    expect(newerDraft(null, null)).toBeNull();
  });
});
