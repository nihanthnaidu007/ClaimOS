// FNOL wizard flow tests — the orchestrator behind MSW: per-step validation
// gates, draft restore/resume from localStorage, and the live policy lookup
// verdict blocking Next on dead policy numbers.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FNOLWizard from './FNOLWizard';
import { DRAFT_KEY } from './drafts';

function renderWizard(props = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FNOLWizard {...props} />
    </QueryClientProvider>
  );
}

// Fills step 0 with a valid incident and advances to the Policy Holder step.
async function advancePastIncident() {
  fireEvent.change(screen.getByTestId('incident-type'), { target: { value: 'accident' } });
  fireEvent.change(screen.getByTestId('incident-date'), { target: { value: '2026-09-01' } });
  fireEvent.change(screen.getByTestId('estimated-cost'), { target: { value: '1200' } });
  fireEvent.change(screen.getByTestId('incident-description'), {
    target: { value: 'A driver rear-ended my parked car this morning.' },
  });
  fireEvent.click(screen.getByRole('button', { name: /next/i }));
  await screen.findByTestId('wizard-step-1');
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('FNOLWizard — validation gates', () => {
  it('blocks Next on an empty incident step and shows inline errors', async () => {
    renderWizard();
    await screen.findByTestId('wizard-step-0');

    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    expect(await screen.findByText('Select the incident type')).toBeInTheDocument();
    expect(screen.getByTestId('wizard-step-0')).toBeInTheDocument();
  });

  it('advances to the Policy Holder step once the incident is valid', async () => {
    renderWizard();
    await screen.findByTestId('wizard-step-0');

    await advancePastIncident();

    expect(screen.getByTestId('policy-number-input')).toBeInTheDocument();
  });
});

describe('FNOLWizard — draft resume', () => {
  it('restores a saved local draft with a resume banner', async () => {
    window.localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({
        data: { incidentType: 'theft', description: 'Bike stolen from the station rack.' },
        step: null,
        savedAt: new Date().toISOString(),
        draftId: 'draft_saved42',
      })
    );

    renderWizard();

    const banner = await screen.findByTestId('draft-resume-banner');
    expect(banner).toBeInTheDocument();
    expect(screen.getByTestId('incident-type')).toHaveValue('theft');
  });

  it('discard clears the draft and resets the form', async () => {
    window.localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({
        data: { incidentType: 'theft' },
        step: null,
        savedAt: new Date().toISOString(),
        draftId: 'draft_saved42',
      })
    );

    renderWizard();
    await screen.findByTestId('draft-resume-banner');

    fireEvent.click(screen.getByTestId('draft-discard-btn'));

    await waitFor(() => expect(screen.queryByTestId('draft-resume-banner')).not.toBeInTheDocument());
    expect(screen.getByTestId('incident-type')).toHaveValue('');
    expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  it('shows no banner when no draft exists', async () => {
    renderWizard();
    await screen.findByTestId('wizard-step-0');
    expect(screen.queryByTestId('draft-resume-banner')).not.toBeInTheDocument();
  });
});

describe('FNOLWizard — live policy lookup gate', () => {
  it('blocks Next while the policy number is unknown (regression: verdict gate)', async () => {
    renderWizard();
    await screen.findByTestId('wizard-step-0');
    await advancePastIncident();

    fireEvent.change(screen.getByTestId('policy-number-input'), { target: { value: 'POL-DEAD' } });
    await waitFor(
      () => expect(screen.getByTestId('policy-number-feedback')).toHaveTextContent('No policy found'),
      { timeout: 3000 }
    );

    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(await screen.findByTestId('policy-number-error')).toHaveTextContent('No policy found for POL-DEAD');
    expect(screen.getByTestId('wizard-step-1')).toBeInTheDocument();
  });

  it('advances through a known active policy with inline coverage facts', async () => {
    renderWizard();
    await screen.findByTestId('wizard-step-0');
    await advancePastIncident();

    fireEvent.change(screen.getByTestId('policy-number-input'), { target: { value: 'POL-2024-001847' } });
    const panel = await screen.findByTestId('policy-found-panel', {}, { timeout: 3000 });
    expect(panel).toHaveTextContent('Sarah Chen');

    fireEvent.change(screen.getByTestId('holder-name'), { target: { value: 'Sarah Chen' } });
    fireEvent.change(screen.getByTestId('holder-email'), { target: { value: 'sarah@example.com' } });
    fireEvent.change(screen.getByTestId('incident-role'), { target: { value: 'policyholder' } });
    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    await screen.findByTestId('wizard-step-2');
  });

  it('hands a valid submission to onSubmit and clears the draft', async () => {
    const onSubmit = vi.fn().mockResolvedValue();
    renderWizard({ onSubmit });
    await screen.findByTestId('wizard-step-0');
    await advancePastIncident();

    fireEvent.change(screen.getByTestId('policy-number-input'), { target: { value: 'POL-2024-001847' } });
    await screen.findByTestId('policy-found-panel', {}, { timeout: 3000 });
    fireEvent.change(screen.getByTestId('holder-name'), { target: { value: 'Sarah Chen' } });
    fireEvent.change(screen.getByTestId('holder-email'), { target: { value: 'sarah@example.com' } });
    fireEvent.change(screen.getByTestId('incident-role'), { target: { value: 'policyholder' } });
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    await screen.findByTestId('wizard-step-2');

    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    await screen.findByTestId('wizard-step-3');

    fireEvent.click(screen.getByTestId('submit-claim-btn'));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ policyNumber: 'POL-2024-001847', estimatedCost: 1200 }),
      expect.anything()
    );
  });
});
