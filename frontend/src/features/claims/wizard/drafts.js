// Draft model + persistence: localStorage always, server draft for
// cross-device resume. Pure functions — persistence effects live in the hook.
export const DRAFT_KEY = 'claimos.fnol.draft.v1';

export const EMPTY_DRAFT = {
  policyNumber: '',
  holderName: '',
  holderEmail: '',
  incidentRole: 'policyholder',
  incidentType: '',
  incidentDate: '',
  description: '',
  estimatedCost: '',
};

export function createDraft(data) {
  return { ...EMPTY_DRAFT, ...(data || {}) };
}

export function loadLocalDraft() {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return createDraft(parsed.data);
  } catch {
    return null;
  }
}

export function saveLocalDraft(data, step) {
  try {
    const payload = { data, step, savedAt: new Date().toISOString() };
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
  } catch {
    // Private-mode browsers throw on setItem — drafts degrade to session-only.
  }
}

export function clearLocalDraft() {
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    // Nothing to clear is fine.
  }
}

// The draft id travels with the local copy so the server copy is addressable.
export function ensureDraftId() {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed?.draftId) return parsed.draftId;
    }
  } catch {
    // Fall through to a fresh id.
  }
  const id = `draft_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  return id;
}

// Server copy wins only when it is strictly newer (it may come from another device).
export function newerDraft(server, local) {
  if (!server?.data) return local || null;
  if (!local) return createDraft(server.data);
  const serverAt = server.updatedAt ? Date.parse(server.updatedAt) : 0;
  const localAt = local.savedAt ? Date.parse(local.savedAt) : 0;
  return serverAt > localAt ? createDraft(server.data) : local;
}
