// Draft state hook: autosaves to localStorage on every change and mirrors to
// the server draft endpoint (debounced) for cross-device resume. Restores the
// newer of the two copies on mount. All persistence effects live here; the
// draft model itself is pure (see drafts.js).
import { useEffect, useRef, useState } from 'react';
import api from '@/lib/api';
import {
  createDraft, loadLocalDraft, saveLocalDraft, clearLocalDraft,
  ensureDraftId, newerDraft,
} from './drafts';

const LOCAL_SAVE_DELAY = 300;
const SERVER_SAVE_DELAY = 1200;

function EMPTY_INITIALIZER() {
  return createDraft(null);
}

export default function useWizardDraft({ enabled = true } = {}) {
  const [draft, setDraft] = useState(EMPTY_INITIALIZER);
  const [restored, setRestored] = useState(false); // true once mount-time restore has run
  const [resumeDraft, setResumeDraft] = useState(null); // restored copy shown in the resume banner
  const draftIdRef = useRef(null);
  const serverTimer = useRef(null);
  const localTimer = useRef(null);
  const latest = useRef(draft);
  latest.current = draft;

  // Mount: restore the newer of localStorage / server draft, then announce it.
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const restore = async () => {
      draftIdRef.current = ensureDraftId();
      const local = loadLocalDraft();
      let chosen = local;
      try {
        const { data } = await api.get(`/fnol/drafts/${draftIdRef.current}`);
        chosen = newerDraft(data, local ? { ...local, savedAt: local.savedAt } : null);
      } catch {
        // No server copy (404/offline) — the local draft is the truth.
      }
      if (cancelled) return;
      if (chosen && Object.keys(chosen).length > 0) {
        setDraft(createDraft(chosen));
        setResumeDraft(chosen);
      }
      setRestored(true);
    };
    restore();
    return () => { cancelled = true; };
  }, [enabled]);

  // Autosave on change (debounced, both layers).
  useEffect(() => {
    if (!enabled || !restored) return undefined;
    window.clearTimeout(localTimer.current);
    localTimer.current = window.setTimeout(() => {
      saveLocalDraft(latest.current, null);
    }, LOCAL_SAVE_DELAY);
    window.clearTimeout(serverTimer.current);
    serverTimer.current = window.setTimeout(() => {
      api.put(`/fnol/drafts/${draftIdRef.current}`, { data: latest.current }).catch(() => {
        // Server draft is best-effort; localStorage remains the fallback.
      });
    }, SERVER_SAVE_DELAY);
    return () => {
      window.clearTimeout(localTimer.current);
      window.clearTimeout(serverTimer.current);
    };
  }, [draft, restored, enabled]);

  const update = (patch) => setDraft((d) => ({ ...d, ...patch }));

  const discard = () => {
    clearLocalDraft();
    setDraft(createDraft(null));
    setResumeDraft(null);
    if (draftIdRef.current) {
      api.delete(`/fnol/drafts/${draftIdRef.current}`).catch(() => {});
    }
  };

  const dismissResume = () => setResumeDraft(null);

  // Final submit clears the draft everywhere.
  const finalize = async () => {
    clearLocalDraft();
    try {
      if (draftIdRef.current) await api.delete(`/fnol/drafts/${draftIdRef.current}`);
    } catch {
      // Draft cleanup is cosmetic after a successful claim.
    }
  };

  return { draft, update, restored, resumeDraft, discard, dismissResume, finalize };
}
