// Document checklists panel (spec F3): the adjuster's "documents we need".
//
// One request per document the claimant should provide; each tracks to
// received (F4 customer upload) or waived. The four async states follow the
// UX quality bar: skeleton rows at final row height while loading, an
// instructive empty state, a named error with Retry that keeps any last good
// data visible, and the list itself. The create form validates inline on
// blur with the same length limits the backend enforces — a rejected submit
// never clears the user's entries.
import { useEffect, useRef, useState } from 'react';
import {
  Ban,
  CheckCircle2,
  Clock,
  ListChecks,
  Loader2,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react';
import api from '@/lib/api';

// Mirrors backend/app/document_requests.py — keep the two in sync.
const TITLE_MAX = 120;
const DESCRIPTION_MAX = 2000;

// One hue, one meaning (UX bar §5): amber = waiting on the claimant,
// green = done, muted = closed without an upload. Icons ride beside the
// label so color is never the only carrier of the status.
const STATUS_PRESENTATION = {
  requested: {
    label: 'Requested',
    icon: Clock,
    className: 'border-[#f59e0b]/40 bg-[#f59e0b]/10 text-[#f59e0b]',
  },
  received: {
    label: 'Received',
    icon: CheckCircle2,
    className: 'border-[#10b981]/40 bg-[#10b981]/10 text-[#10b981]',
  },
  waived: {
    label: 'Waived',
    icon: Ban,
    className: 'border-[#8b96ab]/40 bg-[#8b96ab]/10 text-[#8b96ab]',
  },
};

function StatusChip({ status }) {
  const presentation = STATUS_PRESENTATION[status] || STATUS_PRESENTATION.requested;
  const Icon = presentation.icon;
  return (
    <span
      data-testid="docreq-status"
      data-status={status}
      className={`inline-flex items-center gap-1 border rounded px-1.5 py-0.5 text-[11px] font-mono uppercase ${presentation.className}`}
    >
      <Icon className="w-3 h-3" aria-hidden />
      {presentation.label}
    </span>
  );
}

function validateTitle(value) {
  const trimmed = value.trim();
  if (!trimmed) return 'Give the document a name — for example “Repair estimate”.';
  if (trimmed.length > TITLE_MAX) return `Keep the title to ${TITLE_MAX} characters or fewer.`;
  return null;
}

function validateDescription(value) {
  if (value.length > DESCRIPTION_MAX) {
    return `Keep the description to ${DESCRIPTION_MAX} characters or fewer.`;
  }
  return null;
}

// FastAPI 422s name the failing field in detail[].loc; map those back to the
// responsible input. Anything unmapped falls through to the panel banner.
function fieldErrorFrom422(error, field) {
  const detail = error?.response?.data?.detail;
  if (!Array.isArray(detail)) return null;
  const hit = detail.find(
    (entry) => Array.isArray(entry?.loc) && entry.loc[entry.loc.length - 1] === field
  );
  return hit ? `${hit.msg}.` : null;
}

function RequestRow({ request, onEdit, onWaive, busy }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(request.title);
  const [description, setDescription] = useState(request.description || '');
  const [titleError, setTitleError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState(null);

  useEffect(() => {
    if (editing) return;
    setTitle(request.title);
    setDescription(request.description || '');
  }, [request.title, request.description, editing]);

  const actionable = request.status === 'requested';

  async function handleSave() {
    const error = validateTitle(title);
    setTitleError(error);
    if (error) return;
    setSaving(true);
    setActionError(null);
    try {
      await onEdit(request.id, { title, description });
      setEditing(false);
    } catch (err) {
      setActionError(
        err?.response?.status === 409
          ? err.response.data?.detail || 'This request was already updated — refresh and try again.'
          : 'The change did not save. Your entries are intact — try again.'
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleWaive() {
    if (!actionable || busy) return;
    setActionError(null);
    try {
      await onWaive(request.id);
    } catch (err) {
      setActionError(
        err?.response?.status === 409
          ? err.response.data?.detail || 'This request can no longer be waived.'
          : 'Could not waive this request. Try again.'
      );
    }
  }

  return (
    <li
      key={request.id}
      data-testid="docreq-item"
      data-status={request.status}
      className="bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-3"
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <p className="text-sm text-[#e2e8f0]" data-testid="docreq-item-title">
            {request.title}
          </p>
          {request.description && (
            <p className="mt-1 text-xs text-[#8b96ab]" data-testid="docreq-item-description">
              {request.description}
            </p>
          )}
        </div>
        <StatusChip status={request.status} />
      </div>
      <div className="mt-2 flex items-center justify-between gap-3 flex-wrap">
        <span className="text-xs font-mono text-[#8b96ab]">
          Requested {new Date(request.created_at).toLocaleDateString()}
        </span>
        {actionable && !editing && (
          <span className="flex items-center gap-2">
            <button
              type="button"
              data-testid="docreq-edit"
              onClick={() => setEditing(true)}
              className="text-xs text-[#7cb0ff] hover:underline"
            >
              Edit
            </button>
            <button
              type="button"
              data-testid="docreq-waive"
              onClick={handleWaive}
              disabled={busy}
              className="text-xs text-[#8b96ab] hover:text-[#e2e8f0] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Waive request
            </button>
          </span>
        )}
      </div>
      {actionError && (
        <p className="mt-2 text-xs text-[#ef4444]" role="alert" data-testid="docreq-action-error">
          {actionError}
        </p>
      )}
      {editing && (
        <div className="mt-3 space-y-2 border-t border-[#1a1f2e] pt-3">
          <label className="block text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
            Title
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => setTitleError(validateTitle(title))}
              data-testid="docreq-edit-title"
              aria-invalid={Boolean(titleError)}
              aria-describedby={titleError ? 'docreq-edit-title-error' : undefined}
              className="mt-1 w-full bg-[#0a0c12] border border-[#1a1f2e] rounded-md px-3 py-2 text-sm text-[#e2e8f0] normal-case tracking-normal focus:outline-none focus:border-[#3b82f6]"
            />
          </label>
          {titleError && (
            <p
              id="docreq-edit-title-error"
              className="text-xs text-[#ef4444] flex items-center gap-1.5"
              data-testid="docreq-edit-title-error"
            >
              <TriangleAlert className="w-3 h-3" aria-hidden /> {titleError}
            </p>
          )}
          <label className="block text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
            Description (optional)
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              data-testid="docreq-edit-description"
              rows={2}
              maxLength={DESCRIPTION_MAX}
              className="mt-1 w-full bg-[#0a0c12] border border-[#1a1f2e] rounded-md px-3 py-2 text-sm text-[#e2e8f0] normal-case tracking-normal focus:outline-none focus:border-[#3b82f6]"
            />
          </label>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              data-testid="docreq-cancel"
              onClick={() => setEditing(false)}
              className="px-3 py-1.5 text-xs rounded-md border border-[#1a1f2e] text-[#8b96ab] hover:text-[#e2e8f0] transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="docreq-save"
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-medium rounded-md px-3 py-1.5 transition-colors"
            >
              {saving && <Loader2 className="w-3 h-3 animate-spin" aria-hidden />}
              {saving ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

export default function DocumentRequests({ claimId }) {
  const [requests, setRequests] = useState(null); // null = loading
  const [loadError, setLoadError] = useState(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);
  const [waivingId, setWaivingId] = useState(null);
  const titleRef = useRef(null);
  const descriptionRef = useRef(null);

  async function loadRequests() {
    setLoadError(null);
    try {
      const response = await api.get(
        `/claims/${encodeURIComponent(claimId)}/document-requests`
      );
      setRequests(response.data);
    } catch {
      // Last good data stays on screen under the error line (UX bar §1).
      setLoadError('Couldn’t load the document checklist. Check your connection and try again.');
    }
  }

  useEffect(() => {
    setRequests(null);
    loadRequests();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimId]);

  function handleBlur(field) {
    const error =
      field === 'title' ? validateTitle(title) : validateDescription(description);
    setFieldErrors((prev) => ({ ...prev, [field]: error }));
  }

  async function handleCreate(event) {
    event.preventDefault();
    if (creating) return; // double-submit guard
    setCreateError(null);
    const errors = {
      title: validateTitle(title),
      description: validateDescription(description),
    };
    setFieldErrors(errors);
    const firstInvalid = errors.title ? titleRef : errors.description ? descriptionRef : null;
    if (firstInvalid) {
      firstInvalid.current?.focus();
      return;
    }
    setCreating(true);
    try {
      await api.post(`/claims/${encodeURIComponent(claimId)}/document-requests`, {
        title: title.trim(),
        description: description.trim(),
      });
      setTitle('');
      setDescription('');
      setFieldErrors({});
      await loadRequests();
    } catch (err) {
      const mapped = {
        title: fieldErrorFrom422(err, 'title'),
        description: fieldErrorFrom422(err, 'description'),
      };
      if (mapped.title || mapped.description) {
        setFieldErrors({ ...errors, ...mapped, title: mapped.title || errors.title });
      }
      setCreateError(
        'We couldn’t save this request. Your entries are safe — try again.'
      );
    } finally {
      setCreating(false);
    }
  }

  async function handleEdit(requestId, patch) {
    await api.patch(
      `/claims/${encodeURIComponent(claimId)}/document-requests/${encodeURIComponent(requestId)}`,
      patch
    );
    await loadRequests();
  }

  async function handleWaive(requestId) {
    setWaivingId(requestId);
    try {
      await api.patch(
        `/claims/${encodeURIComponent(claimId)}/document-requests/${encodeURIComponent(requestId)}`,
        { waive: true }
      );
      await loadRequests();
    } finally {
      setWaivingId(null);
    }
  }

  return (
    <section className="mt-6" data-testid="document-requests">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e2e8f0] uppercase tracking-wider">
        <ListChecks className="w-4 h-4 text-[#f59e0b]" aria-hidden /> Documents we need
      </h2>

      {loadError && (
        <div className="mt-3">
          <p className="text-sm text-[#ef4444]" role="alert" data-testid="docreq-error">
            {loadError}
          </p>
          <button
            type="button"
            onClick={loadRequests}
            data-testid="docreq-retry"
            className="mt-2 inline-flex items-center gap-1.5 text-sm text-[#7cb0ff] hover:underline"
          >
            <RefreshCw className="w-3.5 h-3.5" aria-hidden /> Retry
          </button>
        </div>
      )}

      {!requests && !loadError && (
        <div className="mt-3 space-y-2" data-testid="docreq-skeleton" aria-busy="true">
          {[0, 1].map((i) => (
            <div
              key={i}
              className="h-[76px] bg-[#0d1119] border border-[#1a1f2e] rounded-lg animate-pulse"
            />
          ))}
        </div>
      )}

      {requests && requests.length === 0 && (
        <p className="mt-3 text-sm text-[#8b96ab]" data-testid="docreq-empty">
          No documents requested yet. Add the first one below — for example a repair
          estimate or photos of the damage.
        </p>
      )}

      {requests && requests.length > 0 && (
        <ul className="mt-3 space-y-2" data-testid="docreq-list">
          {requests.map((request) => (
            <RequestRow
              key={request.id}
              request={request}
              onEdit={handleEdit}
              onWaive={handleWaive}
              busy={waivingId === request.id}
            />
          ))}
        </ul>
      )}

      <form onSubmit={handleCreate} className="mt-4 space-y-2" data-testid="docreq-form">
        <label className="block text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
          Request a document
          <input
            ref={titleRef}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => handleBlur('title')}
            data-testid="docreq-title-input"
            placeholder="Repair estimate"
            maxLength={TITLE_MAX}
            aria-invalid={Boolean(fieldErrors.title)}
            aria-describedby={fieldErrors.title ? 'docreq-title-error' : undefined}
            className="mt-1 w-full bg-[#0a0c12] border border-[#1a1f2e] rounded-md px-3 py-2 text-sm text-[#e2e8f0] normal-case tracking-normal focus:outline-none focus:border-[#3b82f6]"
          />
        </label>
        {fieldErrors.title && (
          <p
            id="docreq-title-error"
            className="text-xs text-[#ef4444] flex items-center gap-1.5"
            data-testid="docreq-title-error"
          >
            <TriangleAlert className="w-3 h-3" aria-hidden /> {fieldErrors.title}
          </p>
        )}
        <label className="block text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
          What a good upload looks like (optional)
          <textarea
            ref={descriptionRef}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onBlur={() => handleBlur('description')}
            data-testid="docreq-description-input"
            rows={2}
            placeholder="Signed, itemized, on shop letterhead."
            maxLength={DESCRIPTION_MAX}
            aria-invalid={Boolean(fieldErrors.description)}
            aria-describedby={fieldErrors.description ? 'docreq-description-error' : undefined}
            className="mt-1 w-full bg-[#0a0c12] border border-[#1a1f2e] rounded-md px-3 py-2 text-sm text-[#e2e8f0] normal-case tracking-normal focus:outline-none focus:border-[#3b82f6]"
          />
        </label>
        {fieldErrors.description && (
          <p
            id="docreq-description-error"
            className="text-xs text-[#ef4444] flex items-center gap-1.5"
            data-testid="docreq-description-error"
          >
            <TriangleAlert className="w-3 h-3" aria-hidden /> {fieldErrors.description}
          </p>
        )}
        {createError && (
          <p className="text-sm text-[#ef4444]" role="alert" data-testid="docreq-create-error">
            {createError}
          </p>
        )}
        <button
          type="submit"
          disabled={creating}
          data-testid="docreq-submit"
          className="inline-flex items-center gap-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-md px-4 py-2 transition-colors"
        >
          {creating && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}
          {creating ? 'Requesting…' : 'Request document'}
        </button>
      </form>
    </section>
  );
}
