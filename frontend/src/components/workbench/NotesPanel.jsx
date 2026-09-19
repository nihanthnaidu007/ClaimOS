// Internal notes (F11): adjuster-only working notes on a claim.
//
// Data comes from the adjuster-gated /workbench/claims/:id/notes endpoints.
// The list is refetched after every successful submit so the panel always
// reflects the server; bodies render as plain text (whitespace preserved,
// never HTML) and mention chips are a client-side hint — the server owns the
// authoritative mention resolution against real adjuster accounts.
import { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw, StickyNote } from 'lucide-react';
import api from '@/lib/api';
import { formatDateTime } from '@/lib/workbench';

const NOTE_MAX_LENGTH = 4000; // keep in sync with backend NOTE_MAX_LENGTH

// Same handle shape the backend parses: "@" + local-part chars, trailing
// sentence punctuation stripped.
const MENTION_RE = /(?<![a-zA-Z0-9._-])@([a-zA-Z0-9._-]+)/g;

function detectMentions(text) {
  const handles = new Set();
  for (const match of text.matchAll(MENTION_RE)) {
    const handle = match[1].replace(/\.+$/, '');
    if (handle) handles.add(handle);
  }
  return [...handles];
}

export default function NotesPanel({ claimId }) {
  const [notes, setNotes] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get(`/workbench/claims/${encodeURIComponent(claimId)}/notes`);
      setNotes(data.notes ?? []);
    } catch {
      setError('Could not load the notes for this claim.');
    } finally {
      setLoading(false);
    }
  }, [claimId]);

  useEffect(() => {
    load();
  }, [load]);

  async function submit(e) {
    e.preventDefault();
    const body = draft.trim();
    if (!body || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api.post(`/workbench/claims/${encodeURIComponent(claimId)}/notes`, { body });
      setDraft('');
      await load(); // refetch — the server response is the source of truth
    } catch (err) {
      setSubmitError(
        err?.response?.status === 422
          ? err.response.data?.detail || 'Notes cannot be blank and are capped at 4000 characters.'
          : 'Could not add the note. Try again.'
      );
    } finally {
      setSubmitting(false);
    }
  }

  const mentions = detectMentions(draft);

  return (
    <section className="mt-6" data-testid="notes-panel">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e2e8f0] uppercase tracking-wider">
        <StickyNote className="w-4 h-4 text-[#7cb0ff]" aria-hidden /> Internal notes
      </h2>
      <p className="mt-1 text-xs text-[#8b96ab]">
        Adjuster-only working notes. Never shown to customers. Mention a colleague with @handle
        (the part before the @ in their email) to ring their bell.
      </p>

      {loading && (
        <p className="mt-3 text-sm text-[#8b96ab] flex items-center gap-2" data-testid="notes-loading" aria-busy="true">
          <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> Loading notes…
        </p>
      )}
      {error && (
        <div className="mt-3" data-testid="notes-error">
          <p className="text-sm text-[#ef4444]" role="alert">{error}</p>
          <button type="button" onClick={load} data-testid="notes-retry" className="mt-2 inline-flex items-center gap-1.5 text-sm text-[#7cb0ff] hover:underline">
            <RefreshCw className="w-3.5 h-3.5" aria-hidden /> Retry
          </button>
        </div>
      )}
      {!loading && !error && notes && notes.length === 0 && (
        <p className="mt-3 text-sm text-[#8b96ab]" data-testid="notes-empty">
          No internal notes yet. Use the composer below to record what you verified and why.
        </p>
      )}
      {!loading && !error && notes && notes.length > 0 && (
        <ul className="mt-3 space-y-2" data-testid="notes-list">
          {notes.map((note) => (
            <li key={note.id} data-testid="note-item" className="bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-3">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <span className="text-sm text-[#e2e8f0]">{note.authorEmail}</span>
                <span className="text-xs font-mono text-[#8b96ab]">{formatDateTime(note.createdAt)}</span>
              </div>
              {(note.mentions || []).length > 0 && (
                <p className="mt-1 text-xs font-mono text-[#7cb0ff]" data-testid="note-mentions">
                  {note.mentions.map((m) => `@${m}`).join(' ')}
                </p>
              )}
              <p className="mt-2 text-sm text-[#c3cad8] whitespace-pre-wrap" data-testid="note-body">
                {note.body}
              </p>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={submit} className="mt-4" data-testid="note-composer">
        {mentions.length > 0 && (
          <p className="mb-2 flex flex-wrap gap-1.5" data-testid="mention-chips">
            {mentions.map((m) => (
              <span key={m} data-testid="mention-chip" className="inline-block border border-[#7cb0ff]/40 bg-[#7cb0ff]/10 text-[#7cb0ff] rounded px-1.5 py-0.5 text-[11px] font-mono">
                @{m}
              </span>
            ))}
          </p>
        )}
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={NOTE_MAX_LENGTH}
          rows={3}
          placeholder="Add an internal note…"
          data-testid="note-input"
          aria-label="Internal note"
          className="w-full bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-3 text-sm text-[#e2e8f0] placeholder:text-[#8b96ab] focus:outline-none focus:border-[#7cb0ff]"
        />
        <div className="mt-2 flex items-center justify-between gap-3 flex-wrap">
          <span className="text-xs font-mono text-[#8b96ab]" data-testid="note-counter">
            {draft.length}/{NOTE_MAX_LENGTH}
          </span>
          <button
            type="submit"
            disabled={submitting || !draft.trim()}
            data-testid="note-submit"
            className="inline-flex items-center gap-2 bg-[#7c3aed] hover:bg-[#6d28d9] disabled:opacity-50 disabled:hover:bg-[#7c3aed] text-white text-sm font-medium rounded-md px-4 py-2 transition-colors"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : null} Add note
          </button>
        </div>
        {submitError && (
          <p className="mt-2 text-sm text-[#ef4444]" role="alert" data-testid="note-submit-error">
            {submitError}
          </p>
        )}
      </form>
    </section>
  );
}
