// Override modal tests: the required-reason gate mirrors the backend's 422,
// and every successful submit posts the decision/reason/payload contract.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import OverrideModal from './OverrideModal';
import { server } from '../../test/handlers';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;

function renderModal(props = {}) {
  const view = render(<OverrideModal claimId="CLM-1001" open onClose={vi.fn()} {...props} />);
  return view;
}

describe('OverrideModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when closed', () => {
    render(<OverrideModal claimId="CLM-1001" open={false} onClose={vi.fn()} />);
    expect(screen.queryByTestId('override-modal')).not.toBeInTheDocument();
  });

  it('disables submit until a reason has content', () => {
    renderModal();

    const submit = screen.getByTestId('override-submit');
    expect(submit).toBeDisabled();
    expect(screen.getByTestId('override-reason-hint')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('override-reason'), { target: { value: '   ' } });
    expect(submit).toBeDisabled(); // whitespace-only is not a reason

    fireEvent.change(screen.getByTestId('override-reason'), { target: { value: 'Verified damage photos myself' } });
    expect(submit).toBeEnabled();
  });

  it('posts the decision with a trimmed reason and optional payout, then closes', async () => {
    const onClose = vi.fn();
    const onOverridden = vi.fn();
    const bodies = [];
    server.use(
      http.post(`${API_BASE}/workbench/claims/CLM-1001/override`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({
          claimId: 'CLM-1001',
          status: 'overridden',
          auditEntry: { id: 'aud_1' },
        });
      })
    );

    renderModal({ onClose, onOverridden });
    // The label wraps both the option text and its hint, so match with a regex.
    fireEvent.click(screen.getByLabelText(/Approve claim/));
    fireEvent.change(screen.getByTestId('override-payout'), { target: { value: '18000' } });
    fireEvent.change(screen.getByTestId('override-reason'), {
      target: { value: '  Verified damage photos myself  ' },
    });
    fireEvent.click(screen.getByTestId('override-submit'));

    await waitFor(() => expect(onOverridden).toHaveBeenCalled());
    expect(bodies).toEqual([
      { decision: 'approved', reason: 'Verified damage photos myself', payoutAmount: 18000 },
    ]);
    expect(onClose).toHaveBeenCalled();
  });

  it('surfaces the backend 422 detail and keeps the modal open', async () => {
    const onClose = vi.fn();
    const onOverridden = vi.fn();
    server.use(
      http.post(`${API_BASE}/workbench/claims/CLM-1001/override`, () =>
        HttpResponse.json({ detail: 'Reason is required to override a claim' }, { status: 422 })
      )
    );

    renderModal({ onClose, onOverridden });
    fireEvent.change(screen.getByTestId('override-reason'), { target: { value: 'x' } });
    fireEvent.click(screen.getByTestId('override-submit'));

    await waitFor(() => expect(screen.getByTestId('override-error')).toBeInTheDocument());
    expect(screen.getByTestId('override-error')).toHaveTextContent(
      'Reason is required to override a claim'
    );
    expect(onOverridden).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('surfaces non-422 rejections (e.g. 409 decided claims) with the server detail', async () => {
    server.use(
      http.post(`${API_BASE}/workbench/claims/CLM-1001/override`, () =>
        HttpResponse.json(
          { detail: "Claim is already decided (status 'approved') — re-open it before overriding" },
          { status: 409 }
        )
      )
    );

    renderModal();
    fireEvent.change(screen.getByTestId('override-reason'), { target: { value: 'x' } });
    fireEvent.click(screen.getByTestId('override-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('override-error')).toHaveTextContent(/already decided/)
    );
  });

  it('rejects a negative payout locally before submitting', () => {
    renderModal();
    fireEvent.change(screen.getByTestId('override-payout'), { target: { value: '-5' } });
    fireEvent.change(screen.getByTestId('override-reason'), { target: { value: 'x' } });

    expect(screen.getByTestId('override-payout-error')).toBeInTheDocument();
    expect(screen.getByTestId('override-submit')).toBeDisabled();
  });
});
