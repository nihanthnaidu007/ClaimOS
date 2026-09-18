// Bulk action modal tests (spec F12): the N-claims confirmation, the disabled
// apply state until reason/target are complete, and the payload the modal
// posts — reassign carries a target adjuster, flag does not.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import BulkActionModal from './BulkActionModal';
import { server } from '../../test/handlers';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;

function renderModal(overrides = {}) {
  const props = {
    open: true,
    action: 'flag',
    claimCount: 2,
    claimIds: ['CLM-1001', 'CLM-1002'],
    onApplied: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  render(<BulkActionModal {...props} />);
  return props;
}

describe('BulkActionModal', () => {
  it('shows the N-claim confirmation and disables apply until a reason is entered', () => {
    renderModal();
    expect(screen.getByTestId('bulk-modal')).toBeInTheDocument();
    expect(screen.getByTestId('bulk-claim-count-n')).toHaveTextContent('2');
    expect(screen.getByTestId('bulk-confirm')).toBeDisabled();
    fireEvent.change(screen.getByTestId('bulk-reason'), { target: { value: 'Check invoices' } });
    expect(screen.getByTestId('bulk-confirm')).toBeEnabled();
  });

  it('requires a target adjuster for reassign and includes it in the payload', async () => {
    const onApplied = vi.fn();
    const bulkPosts = [];
    server.use(
      http.post(`${API_BASE}/workbench/claims/bulk`, async ({ request }) => {
        bulkPosts.push(await request.json());
        return HttpResponse.json({
          action: 'reassign',
          results: [
            { claimId: 'CLM-1001', status: 'updated', auditId: 'a1' },
            { claimId: 'CLM-1002', status: 'updated', auditId: 'a2' },
          ],
          updated: 2,
          failed: 0,
        });
      })
    );
    renderModal({ action: 'reassign', onApplied });

    fireEvent.change(screen.getByTestId('bulk-reason'), { target: { value: 'Vacation hand-off' } });
    expect(screen.getByTestId('bulk-confirm')).toBeDisabled(); // no target yet

    fireEvent.change(screen.getByTestId('bulk-target'), { target: { value: 'ops@claimos.dev' } });
    expect(screen.getByTestId('bulk-confirm')).toBeEnabled();
    fireEvent.click(screen.getByTestId('bulk-confirm'));

    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(bulkPosts[0]).toEqual({
      action: 'reassign',
      claimIds: ['CLM-1001', 'CLM-1002'],
      reason: 'Vacation hand-off',
      target: 'ops@claimos.dev',
    });
    const result = onApplied.mock.calls[0][0];
    expect(result).toMatchObject({ action: 'reassign', updated: 2, failed: 0 });
  });

  it('posts the flag payload without a target and reports the outcome', async () => {
    const onApplied = vi.fn();
    const bulkPosts = [];
    server.use(
      http.post(`${API_BASE}/workbench/claims/bulk`, async ({ request }) => {
        bulkPosts.push(await request.json());
        return HttpResponse.json({
          action: 'flag',
          results: [
            { claimId: 'CLM-1001', status: 'updated', auditId: 'a1' },
            { claimId: 'CLM-1002', status: 'updated', auditId: 'a2' },
          ],
          updated: 2,
          failed: 0,
        });
      })
    );
    renderModal({ action: 'flag', onApplied });

    fireEvent.change(screen.getByTestId('bulk-reason'), { target: { value: 'Check invoices' } });
    fireEvent.click(screen.getByTestId('bulk-confirm'));

    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(bulkPosts[0]).toEqual({
      action: 'flag',
      claimIds: ['CLM-1001', 'CLM-1002'],
      reason: 'Check invoices',
    });
    expect(screen.getByTestId('bulk-result')).toHaveTextContent('Flagged 2 claims.');
  });

  it('renders per-claim failures instead of dropping them', async () => {
    server.use(
      http.post(`${API_BASE}/workbench/claims/bulk`, () =>
        HttpResponse.json({
          action: 'flag',
          results: [
            { claimId: 'CLM-1001', status: 'updated', auditId: 'a1' },
            { claimId: 'CLM-1002', status: 'failed', detail: 'Claim not found' },
          ],
          updated: 1,
          failed: 1,
        })
      )
    );
    renderModal();

    fireEvent.change(screen.getByTestId('bulk-reason'), { target: { value: 'Mixed batch' } });
    fireEvent.click(screen.getByTestId('bulk-confirm'));

    await waitFor(() => expect(screen.getByTestId('bulk-result')).toBeInTheDocument());
    expect(screen.getByTestId('bulk-result-failures')).toHaveTextContent('1 failed:');
    expect(screen.getByTestId('bulk-result-failures')).toHaveTextContent('CLM-1002 — Claim not found');
  });

  it('closes without applying on cancel', () => {
    const onClose = vi.fn();
    renderModal({ onClose });
    fireEvent.click(screen.getByTestId('bulk-cancel'));
    expect(onClose).toHaveBeenCalled();
  });
});
