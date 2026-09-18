// Reopen modal tests: gating on a required reason, the trimmed payload, and
// surfaced backend rejections (F14).
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ReopenModal from './ReopenModal';
import api from '@/lib/api';

function renderModal(overrides = {}) {
  const props = {
    claimId: 'CLM-1001',
    open: true,
    onClose: vi.fn(),
    onReopened: vi.fn(),
    ...overrides,
  };
  render(<ReopenModal {...props} />);
  return props;
}

describe('ReopenModal', () => {
  it('renders nothing when closed', () => {
    renderModal({ open: false });
    expect(screen.queryByTestId('reopen-modal')).not.toBeInTheDocument();
  });

  it('explains that no pipeline re-run happens and the decision stands', () => {
    renderModal();
    expect(screen.getByTestId('reopen-modal')).toBeInTheDocument();
    expect(screen.getByText(/Nothing is re-run automatically/i)).toBeInTheDocument();
    expect(screen.getByText(/current decision stands/i)).toBeInTheDocument();
  });

  it('disables submit until the reason has content', () => {
    renderModal();
    expect(screen.getByTestId('reopen-submit')).toBeDisabled();
    fireEvent.change(screen.getByTestId('reopen-reason'), { target: { value: '   ' } });
    expect(screen.getByTestId('reopen-submit')).toBeDisabled();
    fireEvent.change(screen.getByTestId('reopen-reason'), { target: { value: 'New documents arrived.' } });
    expect(screen.getByTestId('reopen-submit')).toBeEnabled();
  });

  it('posts the trimmed reason and closes on success', async () => {
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { claimId: 'CLM-1001', status: 'reopened' } });
    const { onClose, onReopened } = renderModal();

    fireEvent.change(screen.getByTestId('reopen-reason'), {
      target: { value: '  New repair estimates arrived after the decision.  ' },
    });
    fireEvent.click(screen.getByTestId('reopen-submit'));

    await waitFor(() => expect(onReopened).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith('/claims/CLM-1001/reopen', {
      reason: 'New repair estimates arrived after the decision.',
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('surfaces the backend 409 verbatim', async () => {
    vi.spyOn(api, 'post').mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail:
            "Claim status 'pending' cannot be reopened — only decided claims (approved, rejected, overridden, settled, or failed) can reopen",
        },
      },
    });
    renderModal();

    fireEvent.change(screen.getByTestId('reopen-reason'), { target: { value: 'Too soon.' } });
    fireEvent.click(screen.getByTestId('reopen-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('reopen-error')).toHaveTextContent(/cannot be reopened/i)
    );
  });

  it('shows a generic message when the backend fails without a detail', async () => {
    vi.spyOn(api, 'post').mockRejectedValue({ response: { status: 500 } });
    renderModal();

    fireEvent.change(screen.getByTestId('reopen-reason'), { target: { value: 'Reason.' } });
    fireEvent.click(screen.getByTestId('reopen-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('reopen-error')).toHaveTextContent(/Could not reopen/i)
    );
  });
});
