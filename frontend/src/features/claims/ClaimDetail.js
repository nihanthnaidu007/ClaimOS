// Claim detail: the adjuster's glass-box view of one claim.
//
// The record (server truth), the durable trace timeline, uploaded documents,
// and the evidence-pack export — everything the pipeline produced, in one
// place. Reached from the dashboard/history rows; data flows exclusively
// through TanStack Query.
import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, FileDown, FileText, Upload, Loader2 } from 'lucide-react';
import { useClaim, useClaimTrace, useClaimDocuments } from '@/lib/queries';
import api from '@/lib/api';
import DecisionPanel from './DecisionPanel';
import TraceTimeline from './TraceTimeline';
import { downloadBase64Pdf } from './DecisionPanel';
import { formatDollars } from './constants';

const STATUS_PILL = {
  approved: 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/30',
  auto_approved: 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/30',
  rejected: 'bg-[#ef4444]/10 text-[#ef4444] border-[#ef4444]/30',
  under_review: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
  escalate: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
  escalated: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
  failed: 'bg-[#ef4444]/10 text-[#ef4444] border-[#ef4444]/30',
  pending: 'bg-[#8892a4]/10 text-[#8892a4] border-[#8892a4]/30',
};

const TERMINAL = new Set(['approved', 'auto_approved', 'rejected', 'under_review', 'escalate', 'escalated', 'failed']);

function useEvidencePackDownload(claimId) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  const download = async () => {
    setDownloading(true);
    setError('');
    try {
      const { data } = await api.get(`/claims/${claimId}/evidence-pack`);
      await downloadBase64Pdf(data.pdf, data.filename || `evidence-pack-${claimId}.pdf`);
    } catch {
      setError('Evidence pack export failed — try again.');
    } finally {
      setDownloading(false);
    }
  };
  return { download, downloading, error };
}

function DocumentsPanel({ documents, uploading, onUpload }) {
  return (
    <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-4" data-testid="documents-panel">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs uppercase tracking-wider text-[#8892a4] font-mono" style={{ fontFamily: 'Space Grotesk' }}>
          Documents ({documents.length})
        </h3>
        <label className="flex items-center gap-1.5 text-xs font-mono text-[#3b82f6] hover:text-[#2563eb] cursor-pointer transition-colors duration-200">
          {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
          Upload evidence
          <input
            type="file"
            className="hidden"
            accept=".pdf,.png,.jpg,.jpeg"
            data-testid="document-upload-input"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onUpload(file);
              e.target.value = '';
            }}
          />
        </label>
      </div>
      {documents.length === 0 ? (
        <p className="text-xs font-mono text-[#4a5568]">No documents attached to this claim.</p>
      ) : (
        <ul className="space-y-1.5">
          {documents.map((doc) => (
            <li key={doc.id} className="flex items-center gap-2 text-xs font-mono" data-testid="document-row">
              <FileText className="w-3.5 h-3.5 text-[#4a5568]" />
              <span className="text-[#e2e8f0] truncate">{doc.filename}</span>
              <span className="text-[#4a5568] ml-auto shrink-0">
                {doc.documentType === 'evidence_text' ? 'extracted text' : doc.contentType} · {(doc.sizeBytes / 1024).toFixed(1)} KB
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function ClaimDetail() {
  const { id } = useParams();
  const claimQuery = useClaim(id);
  const traceQuery = useClaimTrace(id);
  const documentsQuery = useClaimDocuments(id);
  const pack = useEvidencePackDownload(id);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');

  const claim = claimQuery.data;

  const upload = async (file) => {
    setUploading(true);
    setUploadError('');
    try {
      const body = new FormData();
      body.append('file', file);
      await api.post(`/claims/${id}/documents`, body, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      await documentsQuery.refetch();
    } catch (err) {
      setUploadError(err?.response?.data?.detail || 'Upload failed — check the file type and size (10 MiB cap).');
    } finally {
      setUploading(false);
    }
  };

  if (claimQuery.isLoading) {
    return (
      <div className="page-enter text-xs font-mono text-[#4a5568] py-12" data-testid="claim-detail-loading">
        Loading claim…
      </div>
    );
  }

  if (claimQuery.isError || !claim) {
    return (
      <div className="page-enter" data-testid="claim-detail-error">
        <p className="text-sm text-[#ef4444] font-mono mb-4">Claim not found or failed to load.</p>
        <Link to="/" className="text-xs font-mono text-[#3b82f6] hover:text-[#2563eb]">← Back to dashboard</Link>
      </div>
    );
  }

  const pill = STATUS_PILL[claim.status] || STATUS_PILL.pending;

  return (
    <div className="page-enter" data-testid="claim-detail">
      <div className="flex items-center justify-between mb-6">
        <div>
          <Link to="/" className="flex items-center gap-1 text-xs font-mono text-[#8892a4] hover:text-[#e2e8f0] mb-2 transition-colors duration-200">
            <ArrowLeft className="w-3 h-3" /> Dashboard
          </Link>
          <h1 className="text-2xl font-bold tracking-tight text-[#e2e8f0]" style={{ fontFamily: 'Space Grotesk' }}>
            {claim.id}
          </h1>
          <div className="flex items-center gap-3 mt-1 text-xs font-mono text-[#8892a4]">
            <span className={`px-2 py-0.5 border rounded-sm uppercase ${pill}`} data-testid="claim-status-pill">
              {claim.status.replace(/_/g, ' ')}
            </span>
            <span>{claim.holder_name || 'Unknown holder'}</span>
            <span>· {claim.policy_number}</span>
            <span>· {formatDollars(claim.claimed_amount)}</span>
          </div>
        </div>
        <button
          onClick={pack.download}
          disabled={pack.downloading}
          className="flex items-center gap-2 border border-[#3b82f6]/50 text-[#3b82f6] hover:bg-[#3b82f6]/10 text-sm font-medium px-4 py-2 rounded-none transition-colors duration-200 disabled:opacity-50"
          data-testid="evidence-pack-btn"
        >
          {pack.downloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileDown className="w-4 h-4" />}
          Evidence pack (PDF)
        </button>
      </div>

      {pack.error && (
        <p className="text-xs font-mono text-[#ef4444] mb-4" data-testid="evidence-pack-error">{pack.error}</p>
      )}

      {claim.access_code && (
        <div
          data-testid="access-code-panel"
          className="mb-6 border border-amber-500/30 bg-amber-500/5 p-3"
        >
          <p className="text-xs uppercase tracking-wide text-amber-400 mb-1">
            Status portal access code
          </p>
          <p className="text-[12px] text-[#8892a4] mb-1.5">
            Give this to the claimant to unlock the public status page for
            this claim only.
          </p>
          <code className="inline-block bg-[#0a0c12] border border-[#1a1f2e] px-2.5 py-1 font-mono text-xs text-amber-300 select-all">
            {claim.access_code}
          </code>
        </div>
      )}

      {uploadError && (
        <p className="text-xs font-mono text-[#ef4444] mb-4" data-testid="upload-error">{uploadError}</p>
      )}

      {TERMINAL.has(claim.status) && <DecisionPanel claim={claim} />}

      <div className="mt-6 space-y-6">
        <div>
          <h2 className="text-xs uppercase tracking-wider text-[#8892a4] font-mono mb-3" style={{ fontFamily: 'Space Grotesk' }}>
            Agent trace
          </h2>
          {traceQuery.isLoading ? (
            <div className="text-xs font-mono text-[#4a5568] py-4" data-testid="trace-loading">Loading trace…</div>
          ) : traceQuery.isError ? (
            <div className="text-xs font-mono text-[#ef4444] py-4" data-testid="trace-error">Trace unavailable — try refreshing.</div>
          ) : (
            <TraceTimeline trace={traceQuery.data} />
          )}
        </div>

        <DocumentsPanel
          documents={documentsQuery.data || []}
          uploading={uploading}
          onUpload={upload}
        />
      </div>
    </div>
  );
}
