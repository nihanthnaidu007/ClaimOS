// Case view: the glass-box dossier for one claim.
//
// Everything here is deterministic: the summary is assembled from stored agent
// traces, the timeline replays the claim's durable event stream, and the audit
// trail reads the append-only audit_log. The only write path is the override
// modal, which requires a reason (backend 422 without one).
import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  CheckCircle2,
  FileText,
  Loader2,
  OctagonAlert,
  RefreshCw,
  ScrollText,
} from 'lucide-react';
import api from '@/lib/api';
import {
  formatCurrency,
  formatDateTime,
  formatDuration,
  formatHours,
  severityPresentation,
  slaBadgeText,
  slaPresentation,
  statusClassName,
} from '@/lib/workbench';
import DocumentRequests from './DocumentRequests';
import OverrideModal from './OverrideModal';

const STAGE_ORDER = ['intake', 'policy', 'documents', 'fraud', 'eligibility', 'decision'];

function Stat({ label, value, mono = true }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono">{label}</div>
      <div className={`mt-0.5 text-sm text-[#e2e8f0] ${mono ? 'font-mono' : ''}`}>{value}</div>
    </div>
  );
}

function SummaryRow({ label, value, tone = 'default' }) {
  if (value === null || value === undefined || value === '') return null;
  const toneClass =
    tone === 'good' ? 'text-[#10b981]' : tone === 'bad' ? 'text-[#ef4444]' : 'text-[#e2e8f0]';
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 border-b border-[#1a1f2e] last:border-b-0">
      <span className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono">{label}</span>
      <span className={`text-sm ${toneClass}`}>{value}</span>
    </div>
  );
}

function DecisionLetter({ claimId, hasLetter }) {
  const [letter, setLetter] = useState(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(null);

  async function loadLetter() {
    setError(null);
    setOpen(true);
    try {
      const { data } = await api.get(`/workbench/claims/${encodeURIComponent(claimId)}/letter`);
      setLetter(data);
    } catch (err) {
      setError(
        err?.response?.status === 404
          ? 'No decision letter was recorded for this claim.'
          : 'Could not load the decision letter.'
      );
    }
  }

  if (!hasLetter) return null;
  return (
    <section className="mt-6" data-testid="decision-letter">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e2e8f0] uppercase tracking-wider">
        <FileText className="w-4 h-4 text-[#3b82f6]" aria-hidden /> Decision letter
      </h2>
      {!open && (
        <button
          type="button"
          onClick={loadLetter}
          data-testid="letter-open"
          className="mt-3 inline-flex items-center gap-2 text-sm text-[#7cb0ff] hover:underline"
        >
          <FileText className="w-4 h-4" aria-hidden />
          View decision letter
        </button>
      )}
      {open && !letter && !error && (
        <p className="mt-3 text-sm text-[#8b96ab] flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> Loading letter…
        </p>
      )}
      {error && (
        <p className="mt-3 text-sm text-[#ef4444]" role="alert">
          {error}
        </p>
      )}
      {letter && (
        <div className="mt-3 bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-4">
          {letter.subject && <p className="font-medium text-[#e2e8f0] mb-2">{letter.subject}</p>}
          <pre className="whitespace-pre-wrap text-sm text-[#c3cad8]" data-testid="letter-body">
            {letter.body}
          </pre>
        </div>
      )}
    </section>
  );
}

function DocumentUploads({ claimId }) {
  const [documents, setDocuments] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);

  async function loadDocuments() {
    setLoadError(null);
    try {
      const response = await api.get(`/claims/${encodeURIComponent(claimId)}/documents`);
      setDocuments(response.data);
    } catch {
      setLoadError('Could not load the document list.');
    }
  }

  useEffect(() => {
    loadDocuments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimId]);

  async function handleUpload(event) {
    event.preventDefault();
    if (!file || uploading) return;
    setUploading(true);
    setUploadError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      await api.post(`/claims/${encodeURIComponent(claimId)}/documents`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setFile(null);
      // Re-check: the list refetch is the confirmation the upload persisted.
      await loadDocuments();
    } catch (err) {
      const status = err?.response?.status;
      setUploadError(
        status === 413
          ? 'File exceeds the 10 MB size cap.'
          : status === 415
            ? 'Unsupported format — attach a PDF, PNG, or JPEG.'
            : status === 422
              ? 'That file is empty.'
              : 'Upload failed. Check your connection and try again.'
      );
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="mt-6" data-testid="document-uploads">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e2e8f0] uppercase tracking-wider">
        <FileText className="w-4 h-4 text-[#10b981]" aria-hidden /> Evidence files
      </h2>
      <form onSubmit={handleUpload} className="mt-3 flex items-center gap-3 flex-wrap">
        <label
          htmlFor="document-file"
          className="inline-flex items-center gap-2 text-sm text-[#8b96ab] cursor-pointer hover:text-[#e2e8f0] border border-[#1a1f2e] rounded-md px-3 py-2 bg-[#0d1119]"
        >
          {file ? file.name : 'Choose a file (PDF, PNG, JPEG — 10 MB max)'}
          <input
            id="document-file"
            type="file"
            accept="application/pdf,image/png,image/jpeg"
            data-testid="document-input"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </label>
        <button
          type="submit"
          disabled={!file || uploading}
          data-testid="upload-button"
          className="inline-flex items-center gap-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-md px-4 py-2 transition-colors"
        >
          {uploading ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <CheckCircle2 className="w-4 h-4" aria-hidden />}
          {uploading ? 'Uploading…' : 'Upload'}
        </button>
      </form>
      {uploadError && (
        <p className="mt-2 text-sm text-[#ef4444]" role="alert" data-testid="upload-error">
          {uploadError}
        </p>
      )}
      {loadError && (
        <p className="mt-2 text-sm text-[#ef4444]" role="alert">
          {loadError}
        </p>
      )}
      {documents && documents.length === 0 && (
        <p className="mt-3 text-sm text-[#8b96ab]" data-testid="documents-empty">
          No evidence files attached yet.
        </p>
      )}
      {documents && documents.length > 0 && (
        <ul className="mt-3 space-y-2" data-testid="documents-list">
          {documents.map((doc) => (
            <li
              key={doc.id}
              data-testid="document-item"
              className="bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-3 flex items-center justify-between gap-3 flex-wrap"
            >
              <span className="text-sm text-[#e2e8f0]">{doc.file_name}</span>
              <span className="text-xs font-mono text-[#8b96ab]">
                {(doc.size_bytes / 1024).toFixed(1)} KB · {doc.content_type} · {formatDateTime(doc.uploaded_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AuditTrail({ entries }) {
  return (
    <section className="mt-6" data-testid="audit-trail">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e2e8f0] uppercase tracking-wider">
        <ScrollText className="w-4 h-4 text-[#7c3aed]" aria-hidden /> Audit trail
      </h2>
      {entries.length === 0 ? (
        <p className="mt-3 text-sm text-[#8b96ab]" data-testid="audit-empty">
          No human actions recorded for this claim yet. Overrides and approvals are permanently
          logged here.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {entries.map((entry) => (
            <li
              key={entry.id}
              data-testid="audit-entry"
              className="bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-3"
            >
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <span className="text-sm text-[#e2e8f0]">
                  <span className="font-medium">{entry.actor_email || entry.actor}</span>
                  <span className="text-[#8b96ab]"> {entry.action.replace('_', ' ')}d this claim</span>
                </span>
                <span className="text-xs font-mono text-[#8b96ab]">{formatDateTime(entry.at)}</span>
              </div>
              <p className="mt-2 text-sm text-[#c3cad8]" data-testid="audit-reason">
                “{entry.reason}”
              </p>
              {entry.after?.payoutAmount != null && (
                <p className="mt-1 text-xs font-mono text-[#8b96ab]">
                  payout → {formatCurrency(entry.after.payoutAmount)}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function CaseView() {
  const { claimId } = useParams();
  const [dossier, setDossier] = useState(null); // {summary, events, audit}
  const [loadError, setLoadError] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);

  const loadCase = useCallback(async () => {
    setLoadError(null);
    setNotFound(false);
    setDossier(null);
    try {
      const [summary, events, audit] = await Promise.all([
        api.get(`/workbench/claims/${encodeURIComponent(claimId)}/summary`),
        api.get(`/workbench/claims/${encodeURIComponent(claimId)}/events`),
        api.get(`/workbench/claims/${encodeURIComponent(claimId)}/audit`),
      ]);
      setDossier({
        summary: summary.data,
        events: events.data.events ?? [],
        audit: audit.data ?? [],
      });
    } catch (err) {
      if (err?.response?.status === 404) setNotFound(true);
      else setLoadError('Could not load this case. Check your connection and try again.');
    }
  }, [claimId]);

  useEffect(() => {
    loadCase();
  }, [loadCase]);

  if (notFound) {
    return (
      <div className="p-6 max-w-3xl mx-auto text-center mt-20" data-testid="case-not-found">
        <OctagonAlert className="w-10 h-10 mx-auto mb-3 text-[#8b96ab]" aria-hidden />
        <h1 className="text-lg font-semibold text-[#e2e8f0]">Claim not found</h1>
        <p className="mt-1 text-sm text-[#8b96ab]">
          The claim “{claimId}” does not exist or was removed.
        </p>
        <Link to="/workbench" className="mt-4 inline-block text-sm text-[#7cb0ff] hover:underline">
          Back to the review queue
        </Link>
      </div>
    );
  }

  if (loadError && !dossier) {
    return (
      <div className="p-6 max-w-3xl mx-auto text-center mt-20" data-testid="case-error">
        <OctagonAlert className="w-10 h-10 mx-auto mb-3 text-[#ef4444]" aria-hidden />
        <p className="text-sm text-[#ef4444]" role="alert">
          {loadError}
        </p>
        <button
          type="button"
          onClick={loadCase}
          data-testid="case-retry"
          className="mt-4 inline-flex items-center gap-1.5 text-sm text-[#7cb0ff] hover:underline"
        >
          <RefreshCw className="w-3.5 h-3.5" aria-hidden /> Retry
        </button>
      </div>
    );
  }

  if (!dossier) {
    return (
      <div className="p-6 max-w-6xl mx-auto mt-6 space-y-4" data-testid="case-loading" aria-busy="true">
        <div className="h-8 w-1/3 bg-[#0d1119] rounded animate-pulse" />
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-24 bg-[#0d1119] border border-[#1a1f2e] rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  const { summary, events, audit } = dossier;
  const reviewable = ['escalated', 'pending', 'under_review'].includes(summary.status);

  return (
    <div className="p-6 max-w-6xl mx-auto" data-testid="case-view">
      <Link
        to="/workbench"
        data-testid="back-to-queue"
        className="inline-flex items-center gap-1.5 text-sm text-[#8b96ab] hover:text-[#e2e8f0]"
      >
        <ArrowLeft className="w-4 h-4" aria-hidden /> Review queue
      </Link>

      {/* Header */}
      <div className="mt-4 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-[#e2e8f0] font-mono" style={{ fontFamily: 'Space Grotesk' }}>
            {summary.claimId}
          </h1>
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            <span
              className={`inline-block border rounded px-1.5 py-0.5 text-[11px] font-mono uppercase ${statusClassName(summary.status)}`}
            >
              {(summary.status || '—').replace('_', ' ')}
            </span>
            <span
              className={`inline-block border rounded px-1.5 py-0.5 text-[11px] font-mono ${severityPresentation(summary.severity).className}`}
            >
              {severityPresentation(summary.severity).label}
            </span>
            {(summary.fraudFlags || []).length > 0 && (
              <span
                data-testid="case-fraud-badge"
                title={(summary.fraudFlags || []).map(f => f.detail || f.code).join('; ')}
                className={`inline-block border rounded px-1.5 py-0.5 text-[11px] font-mono font-bold uppercase ${(summary.fraudFlags || []).some(f => f.severity === 'high') ? 'border-[#ef4444]/40 bg-[#ef4444]/10 text-[#ef4444]' : 'border-[#f59e0b]/40 bg-[#f59e0b]/10 text-[#f59e0b]'}`}
              >
                ⚑ Fraud flagged
              </span>
            )}
            {summary.sla && (
              <span
                data-testid="case-sla-badge"
                data-state={summary.sla.state}
                className={`inline-flex items-center gap-1 border rounded px-1.5 py-0.5 text-[11px] font-mono ${slaPresentation(summary.sla).className}`}
              >
                {slaBadgeText(summary.sla)}
              </span>
            )}
            {summary.escalationReason && (
              <span className="inline-block bg-[#f59e0b]/15 text-[#f59e0b] border border-[#f59e0b]/40 rounded px-1.5 py-0.5 text-[11px] font-mono">
                ESCALATED: {summary.escalationReason}
              </span>
            )}
          </div>
        </div>
        {reviewable && (
          <button
            type="button"
            onClick={() => setOverrideOpen(true)}
            data-testid="open-override"
            className="inline-flex items-center gap-2 bg-[#7c3aed] hover:bg-[#6d28d9] text-white text-sm font-medium rounded-md px-4 py-2 transition-colors"
          >
            <CheckCircle2 className="w-4 h-4" aria-hidden /> Record decision
          </button>
        )}
      </div>

      {/* Facts strip */}
      <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-4">
        <Stat label="Holder" value={summary.holderName || '—'} mono={false} />
        <Stat label="Policy" value={summary.policyNumber || '—'} />
        <Stat label="Claimed" value={formatCurrency(summary.claimedAmount)} />
        <Stat label="Risk score" value={summary.riskScore ?? '—'} />
        <Stat label="Incident" value={summary.incidentType || '—'} mono={false} />
        <Stat label="Age" value={formatHours(summary.sla?.hoursElapsed)} />
      </div>

      {/* AI case summary — deterministic, from stored traces */}
      <section className="mt-6" data-testid="case-summary">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e2e8f0] uppercase tracking-wider">
          <CheckCircle2 className="w-4 h-4 text-[#10b981]" aria-hidden /> Case summary
        </h2>
        <p className="text-xs text-[#8b96ab] mt-1">
          Assembled from the stored agent traces — no model call. <span className="font-mono">{summary.source}</span>
        </p>
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          <div className="bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-4">
            <h3 className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono mb-2">Pipeline verdict</h3>
            <SummaryRow
              label="Recommendation"
              value={summary.recommendation}
              tone={summary.recommendation === 'approve' ? 'good' : 'bad'}
            />
            <SummaryRow label="Confidence" value={summary.confidence != null ? `${Math.round(summary.confidence * 100)}%` : null} />
            <SummaryRow label="Decision verdict" value={summary.decision?.verdict} />
            <SummaryRow
              label="Decision payout"
              value={summary.decision?.payoutAmount != null ? formatCurrency(summary.decision.payoutAmount) : null}
            />
            <SummaryRow
              label="Escalation reason"
              value={summary.escalationReason}
              tone={summary.escalationReason ? 'bad' : 'default'}
            />
            {summary.failureReason && <SummaryRow label="Failure reason" value={summary.failureReason} tone="bad" />}
          </div>
          <div className="bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-4">
            <h3 className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono mb-2">Evidence</h3>
            <SummaryRow
              label="Policy found"
              value={
                summary.coverage?.found == null
                  ? null
                  : summary.coverage.found
                    ? `yes (${summary.coverage.statusCheck || 'status unknown'})`
                    : 'no'
              }
              tone={summary.coverage?.found ? 'good' : 'bad'}
            />
            <SummaryRow
              label="Within limits"
              value={summary.coverage?.withinLimits == null ? null : summary.coverage.withinLimits ? 'yes' : 'no'}
              tone={summary.coverage?.withinLimits ? 'good' : 'bad'}
            />
            <SummaryRow
              label="Documents consistency"
              value={summary.documents?.consistencyScore != null ? `${Math.round(summary.documents.consistencyScore * 100)}%` : null}
            />
            <SummaryRow label="Red flags" value={summary.documents?.redFlags?.length ? summary.documents.redFlags.join('; ') : 'none'} tone={summary.documents?.redFlags?.length ? 'bad' : 'good'} />
            <SummaryRow label="Fraud indicators" value={summary.eligibility?.fraudIndicators?.length ? summary.eligibility.fraudIndicators.join('; ') : 'none'} tone={summary.eligibility?.fraudIndicators?.length ? 'bad' : 'good'} />
            <SummaryRow
              label="Risk factors"
              value={summary.eligibility?.riskFactors?.length ? summary.eligibility.riskFactors.join('; ') : 'none'}
            />
          </div>
        </div>
        {summary.override && (
          <div className="mt-3 bg-[#7c3aed]/10 border border-[#7c3aed]/40 rounded-lg p-4" data-testid="override-record">
            <p className="text-sm text-[#b79df5]">
              Human override recorded by <span className="font-medium">{summary.override.actor_email || summary.override.actor}</span>{' '}
              on {formatDateTime(summary.override.at)} — decision “{summary.override.decision}”, reason: “{summary.override.reason}”.
            </p>
          </div>
        )}
      </section>

      {/* Trace timeline */}
      <section className="mt-6" data-testid="trace-timeline">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e2e8f0] uppercase tracking-wider">
          <ScrollText className="w-4 h-4 text-[#3b82f6]" aria-hidden /> Agent trace
        </h2>
        <ol className="mt-3 space-y-2">
          {summary.stages
            ?.slice()
            .sort((a, b) => STAGE_ORDER.indexOf(a.agent) - STAGE_ORDER.indexOf(b.agent))
            .map((stage) => (
              <li
                key={stage.agent}
                data-testid={`trace-stage-${stage.agent}`}
                className="bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm font-medium text-[#e2e8f0]">{stage.label}</span>
                  <span className="flex items-center gap-2 text-xs font-mono text-[#8b96ab]">
                    <span
                      className={`inline-block w-1.5 h-1.5 rounded-full ${
                        stage.status === 'complete' ? 'bg-[#10b981]' : stage.status === 'error' ? 'bg-[#ef4444]' : 'bg-[#8b96ab]'
                      }`}
                    />
                    {stage.reached ? `${stage.status} · ${formatDuration(stage.durationMs)}` : 'not reached'}
                  </span>
                </div>
                {stage.reasoning && <p className="mt-1.5 text-sm text-[#8b96ab]">{stage.reasoning}</p>}
              </li>
            ))}
        </ol>
        {events.length > 0 && (
          <details className="mt-3 bg-[#0d1119] border border-[#1a1f2e] rounded-lg p-3" data-testid="event-log">
            <summary className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono cursor-pointer">
              Full event log ({events.length})
            </summary>
            <ul className="mt-2 space-y-1">
              {events.map((event) => (
                <li key={event.seq ?? `${event.at}-${event.event}`} className="text-xs font-mono text-[#8b96ab]">
                  <span className="text-[#7cb0ff]">#{event.seq}</span> {formatDateTime(event.at)} — {event.event}
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <DecisionLetter claimId={summary.claimId} hasLetter={summary.decision?.hasLetterBody} />

      <DocumentUploads claimId={summary.claimId} />

      <DocumentRequests claimId={summary.claimId} />

      <AuditTrail entries={audit} />

      <OverrideModal
        claimId={summary.claimId}
        open={overrideOpen}
        onClose={() => setOverrideOpen(false)}
        onOverridden={() => loadCase()}
      />
    </div>
  );
}
