import { useRef, useState } from 'react';
import { FileText, Upload, CheckCircle2, CircleDashed, Ban } from 'lucide-react';
import axios from 'axios';

const API = `${import.meta.env.VITE_API_BASE_URL}/api`;

// Mirrors the backend's UPLOAD_ALLOWED_CONTENT_TYPES / UPLOAD_MAX_BYTES
// defaults — client-side prechecks give instant feedback; the server (which
// owns the real contract) rejects anything else the same way it rejects
// adjuster uploads (shared app.uploads validation).
const MAX_BYTES = 10 * 1024 * 1024;
const ACCEPTED_LABEL = 'PDF, PNG, or JPEG';
const ACCEPTED_TYPES = new Set(['application/pdf', 'image/png', 'image/jpeg']);

function precheckError(file) {
  if (!file) return 'Choose a file first.';
  if (!ACCEPTED_TYPES.has(file.type)) {
    return `This file type isn't accepted — please upload a ${ACCEPTED_LABEL} file.`;
  }
  if (file.size > MAX_BYTES) {
    return 'That file is larger than the 10 MB limit — please upload a smaller file.';
  }
  if (file.size === 0) {
    return 'That file is empty — please choose a document with content.';
  }
  return null;
}

// Per-request state text for the four server outcomes a customer can hit.
function uploadErrorMessage(status) {
  switch (status) {
    case 413:
      return 'That file is larger than the 10 MB limit — please upload a smaller file.';
    case 415:
      return `This file type isn't accepted — please upload a ${ACCEPTED_LABEL} file.`;
    case 422:
      return 'That file is empty — please choose a document with content.';
    case 409:
      return 'This request was just updated — refresh the page to see its current state.';
    case 429:
      return 'Too many attempts. Please wait a minute and try again.';
    default:
      return "We couldn't match that request to your claim. Refresh the page and try again.";
  }
}

function RequestRow({ request, credentials, onUploaded }) {
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  const upload = async () => {
    const precheck = precheckError(file);
    if (precheck) {
      setError(precheck);
      return;
    }
    setError('');
    setUploading(true);
    const data = new FormData();
    data.set('claimNumber', credentials.claimNumber);
    data.set('accessCode', credentials.accessCode);
    data.set('requestId', request.id);
    data.set('file', file);
    try {
      await axios.post(`${API}/status/upload-document`, data);
      setFile(null);
      if (inputRef.current) inputRef.current.value = '';
      onUploaded();
    } catch (err) {
      setError(uploadErrorMessage(err.response ? err.response.status : 0));
    } finally {
      setUploading(false);
    }
  };

  const received = request.status === 'received';
  const waived = request.status === 'waived';

  return (
    <li
      data-testid="docreq-item"
      data-status={request.status}
      className="border border-[#1a1f2e] rounded-sm p-4 bg-[#0a0c12]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-[#e2e8f0] flex items-center gap-2">
            <FileText className="w-4 h-4 shrink-0 text-[#8892a4]" />
            {request.title}
          </p>
          {request.description && (
            <p className="text-xs text-[#8892a4] mt-1">{request.description}</p>
          )}
        </div>
        {received && (
          <span
            data-testid="docreq-chip"
            className="shrink-0 inline-flex items-center gap-1 text-xs text-[#34d399] border border-[#34d399]/30 bg-[#34d399]/5 px-2 py-0.5 rounded-sm"
          >
            <CheckCircle2 className="w-3.5 h-3.5" /> Received
          </span>
        )}
        {waived && (
          <span
            data-testid="docreq-chip"
            className="shrink-0 inline-flex items-center gap-1 text-xs text-[#8892a4] border border-[#1a1f2e] bg-[#0f1218] px-2 py-0.5 rounded-sm"
          >
            <Ban className="w-3.5 h-3.5" /> Not needed
          </span>
        )}
        {request.status === 'requested' && (
          <span
            data-testid="docreq-chip"
            className="shrink-0 inline-flex items-center gap-1 text-xs text-[#fbbf24] border border-[#f59e0b]/30 bg-[#f59e0b]/5 px-2 py-0.5 rounded-sm"
          >
            <CircleDashed className="w-3.5 h-3.5" /> Needed
          </span>
        )}
      </div>

      {request.status === 'requested' && (
        <div className="mt-3 flex flex-col sm:flex-row sm:items-center gap-2">
          <input
            ref={inputRef}
            type="file"
            data-testid="docreq-upload-input"
            accept={[...ACCEPTED_TYPES].join(',')}
            onChange={(e) => {
              setFile(e.target.files && e.target.files[0]);
              setError('');
            }}
            className="text-xs text-[#8892a4] file:mr-3 file:rounded-sm file:border-0 file:bg-[#0f1218] file:px-3 file:py-1.5 file:text-xs file:text-[#e2e8f0] file:border file:border-[#1a1f2e] hover:file:border-[#3b82f6] file:cursor-pointer"
          />
          <button
            type="button"
            data-testid="docreq-upload-submit"
            onClick={upload}
            disabled={uploading || !file}
            className="shrink-0 inline-flex items-center gap-1.5 bg-[#3b82f6] hover:bg-[#3b82f6]/90 disabled:opacity-40 text-white rounded-sm font-medium text-xs px-3 py-2 transition-colors duration-200"
          >
            <Upload className="w-3.5 h-3.5" />
            {uploading ? 'Uploading…' : 'Upload'}
          </button>
        </div>
      )}
      {error && (
        <p
          data-testid="docreq-upload-error"
          role="alert"
          className="mt-2 text-xs text-[#f87171]"
        >
          {error}
        </p>
      )}
    </li>
  );
}

// "Documents we need" (spec F4): the claim's document checklist, customer-side.
// `credentials` is the claim number + access code pair from the last lookup
// (the upload's auth), `requests` the lookup payload's documentRequests.
// Hidden entirely when the claim has no checklist — no card means nothing is
// being asked of the customer.
export default function PortalDocumentRequests({ credentials, requests, onUploaded }) {
  if (!credentials || !requests || requests.length === 0) return null;

  return (
    <section
      data-testid="portal-documents-card"
      aria-label="Documents we need"
      className="mt-6"
    >
      <h2 className="text-sm uppercase tracking-wider text-[#8892a4] font-mono mb-3">
        Documents we need
      </h2>
      <ul className="space-y-3">
        {requests.map((request) => (
          <RequestRow
            key={request.id}
            request={request}
            credentials={credentials}
            onUploaded={onUploaded}
          />
        ))}
      </ul>
    </section>
  );
}
