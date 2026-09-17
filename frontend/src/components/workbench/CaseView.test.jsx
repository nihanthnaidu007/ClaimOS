// Case view tests: dossier assembly (summary + events + audit), the audit
// trail rendering, and the not-found path.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import CaseView from './CaseView';
import api from '@/lib/api';
import { server, workbenchCaseSummary } from '../../test/handlers';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;

let consoleErrorSpy;

beforeEach(() => {
  // Component-level failures log via console.error — silence the expected ones.
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  consoleErrorSpy.mockRestore();
});

function renderCase(claimId = 'CLM-1001') {
  return render(
    <MemoryRouter initialEntries={[`/workbench/claims/${claimId}`]}>
      <Routes>
        <Route path="/workbench/claims/:claimId" element={<CaseView />} />
        <Route path="/workbench" element={<div>queue</div>} />
      </Routes>
    </MemoryRouter>
  );
}

const events = [
  { seq: 1, at: '2026-09-15T08:00:00+00:00', event: 'claim_submitted' },
  { seq: 2, at: '2026-09-15T08:00:05+00:00', event: 'claim_escalated' },
];

const audit = [
  {
    id: 'aud_abc123',
    claim_id: 'CLM-1001',
    actor: 'usr_1',
    actor_email: 'adjuster@claimos.test',
    action: 'override',
    before: { status: 'escalated' },
    after: { status: 'overridden', payoutAmount: 18000 },
    reason: 'Verified damage photos myself',
    at: '2026-09-17T09:30:00+00:00',
  },
];

function useCaseHandlers({ withAudit = true } = {}) {
  server.use(
    http.get(`${API_BASE}/workbench/claims/CLM-1001/summary`, () =>
      HttpResponse.json(workbenchCaseSummary)
    ),
    http.get(`${API_BASE}/workbench/claims/CLM-1001/events`, () =>
      HttpResponse.json({ claimId: 'CLM-1001', events })
    ),
    http.get(`${API_BASE}/workbench/claims/CLM-1001/audit`, () =>
      HttpResponse.json(withAudit ? audit : [])
    )
  );
}

describe('CaseView', () => {
  it('renders the deterministic case summary, trace stages, and audit trail', async () => {
    useCaseHandlers();
    renderCase();

    await waitFor(() => expect(screen.getByTestId('case-view')).toBeInTheDocument());
    expect(screen.getByText('CLM-1001')).toBeInTheDocument();
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument();
    expect(screen.getByText('high value claim')).toBeInTheDocument();

    // Deterministic summary badge (glass-box: no LLM call).
    expect(screen.getByText(/Assembled from the stored agent traces/i)).toBeInTheDocument();

    // Trace timeline: all five pipeline stages render.
    expect(screen.getByTestId('trace-stage-intake')).toBeInTheDocument();
    expect(screen.getByTestId('trace-stage-decision')).toBeInTheDocument();
    expect(screen.getByText('not reached')).toBeInTheDocument();

    // Audit trail entry with the reason.
    expect(screen.getByTestId('audit-entry')).toBeInTheDocument();
    expect(screen.getByTestId('audit-reason')).toHaveTextContent('Verified damage photos myself');
  });

  it('offers the decision letter when the trace has one, loading it on click', async () => {
    server.use(
      http.get(`${API_BASE}/workbench/claims/CLM-1001/summary`, () =>
        HttpResponse.json({
          ...workbenchCaseSummary,
          decision: {
            verdict: 'approved',
            payoutAmount: 18000,
            letterSubject: 'Claim decision',
            hasLetterBody: true,
          },
        })
      ),
      http.get(`${API_BASE}/workbench/claims/CLM-1001/letter`, () =>
        HttpResponse.json({ claimId: 'CLM-1001', subject: 'Claim decision', body: 'Dear Ms. Hopper…' })
      ),
      http.get(`${API_BASE}/workbench/claims/CLM-1001/events`, () =>
        HttpResponse.json({ claimId: 'CLM-1001', events: [] })
      ),
      http.get(`${API_BASE}/workbench/claims/CLM-1001/audit`, () => HttpResponse.json([]))
    );
    renderCase();

    await waitFor(() => expect(screen.getByTestId('decision-letter')).toBeInTheDocument());
    expect(screen.queryByTestId('letter-body')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('letter-open'));
    await waitFor(() =>
      expect(screen.getByTestId('letter-body')).toHaveTextContent('Dear Ms. Hopper…')
    );
  });

  it('shows the empty audit state when no human action is recorded', async () => {
    useCaseHandlers({ withAudit: false });
    renderCase();

    await waitFor(() => expect(screen.getByTestId('audit-empty')).toBeInTheDocument());
  });

  it('renders the not-found state for a missing claim', async () => {
    server.use(
      http.get(`${API_BASE}/workbench/claims/CLM-404/summary`, () =>
        HttpResponse.json({ detail: 'Claim not found' }, { status: 404 })
      ),
      // The dossier loads all three endpoints in parallel; mock the survivors
      // so the 404 is the only rejection Promise.all can observe.
      http.get(`${API_BASE}/workbench/claims/CLM-404/events`, () =>
        HttpResponse.json({ claimId: 'CLM-404', events: [] })
      ),
      http.get(`${API_BASE}/workbench/claims/CLM-404/audit`, () => HttpResponse.json([]))
    );
    renderCase('CLM-404');

    await waitFor(() => expect(screen.getByTestId('case-not-found')).toBeInTheDocument());
  });

  it('renders a retryable error state when the dossier fails to load', async () => {
    server.use(
      http.get(`${API_BASE}/workbench/claims/CLM-1001/summary`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 })
      )
    );
    renderCase();

    await waitFor(() => expect(screen.getByTestId('case-error')).toBeInTheDocument());
    expect(screen.getByTestId('case-retry')).toBeInTheDocument();
  });

  it('uploads an evidence file and re-checks the document list', async () => {
    useCaseHandlers();
    const uploadedDoc = {
      id: 'DOC-1',
      claim_id: 'CLM-1001',
      file_name: 'accident-report.pdf',
      content_type: 'application/pdf',
      size_bytes: 324,
      storage_key: 'claims/CLM-1001/doc-1',
      uploaded_at: '2026-09-17T10:05:00+00:00',
      uploaded_by: 'adjuster@claimos.test',
      sha256: 'abc123',
    };
    // jsdom's XHR cannot transport FormData with a File, so the multipart POST
    // is mocked at the axios seam; the real wire is covered by browser dogfood.
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue({ data: uploadedDoc });
    server.use(
      http.get(`${API_BASE}/claims/CLM-1001/documents`, ({ request }) => {
        const after = postSpy.mock.calls.length > 0;
        return HttpResponse.json(after ? [uploadedDoc] : []);
      })
    );
    renderCase();

    // Empty book first — the upload then makes the item appear via the re-check.
    await waitFor(() => expect(screen.getByTestId('documents-empty')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('document-input'), {
      target: {
        files: [new File(['fake-pdf-bytes'], 'accident-report.pdf', { type: 'application/pdf' })],
      },
    });
    fireEvent.click(screen.getByTestId('upload-button'));

    await waitFor(() => expect(screen.getByTestId('document-item')).toBeInTheDocument());
    expect(screen.getByText('accident-report.pdf')).toBeInTheDocument();
    expect(screen.queryByTestId('documents-empty')).not.toBeInTheDocument();
    expect(postSpy).toHaveBeenCalledTimes(1);
  });

  it('surfaces the allowlist rejection for a disallowed upload', async () => {
    useCaseHandlers();
    server.use(
      http.get(`${API_BASE}/claims/CLM-1001/documents`, () => HttpResponse.json([]))
    );
    vi.spyOn(api, 'post').mockRejectedValue({
      response: { status: 415, data: { detail: 'Unsupported content type: text/plain' } },
    });
    renderCase();

    await waitFor(() => expect(screen.getByTestId('documents-empty')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('document-input'), {
      target: {
        files: [new File(['nope'], 'notes.txt', { type: 'text/plain' })],
      },
    });
    fireEvent.click(screen.getByTestId('upload-button'));

    await waitFor(() =>
      expect(screen.getByTestId('upload-error')).toHaveTextContent('Unsupported format')
    );
    // The rejected file is not listed.
    expect(screen.queryByTestId('document-item')).not.toBeInTheDocument();
  });
});
