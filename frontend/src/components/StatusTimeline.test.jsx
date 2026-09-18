// StatusTimeline component test — the public timeline renders only masked
// portal payload fields (first name + status), progresses with milestones,
// and gates the decision-letter download on decision readiness.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import StatusTimeline from './StatusTimeline';

const basePayload = {
  claimNumber: 'CLM-20260917-042',
  status: 'pending',
  statusLabel: 'Under review',
  currentStage: 'DOCUMENT_AGENT',
  decisionReady: false,
  pdfAvailable: false,
  milestones: [
    { key: 'submitted', label: 'Claim submitted', done: true, at: '2026-09-17T10:00:00+00:00' },
    { key: 'documents_received', label: 'Documents received', done: true, at: '2026-09-17T10:05:00+00:00' },
    { key: 'decision_ready', label: 'Decision ready', done: false, at: null },
    { key: 'payout_recorded', label: 'Payout recorded', done: false, at: null },
  ],
};

const renderTimeline = (overrides = {}, handlers = {}) =>
  render(
    <StatusTimeline
      status={{ ...basePayload, ...overrides }}
      onDownloadLetter={handlers.onDownloadLetter}
      downloading={handlers.downloading}
    />
  );

describe('StatusTimeline', () => {
  it('renders claim number, status badge, and milestone progress', () => {
    renderTimeline();

    expect(screen.getByTestId('status-claim-number')).toHaveTextContent('CLM-20260917-042');
    expect(screen.getByTestId('status-badge')).toHaveTextContent('Under review');
    expect(screen.getByTestId('milestone-submitted')).toBeInTheDocument();
    expect(screen.getByTestId('milestone-payout_recorded')).toBeInTheDocument();
    expect(screen.getByText('2 of 4 milestones complete')).toBeInTheDocument();
  });

  it('shows the current processing stage while the claim is in flight', () => {
    renderTimeline();
    expect(screen.getByTestId('status-current-stage')).toHaveTextContent('DOCUMENT_AGENT');
  });

  it('omits the current-stage banner once the pipeline settles', () => {
    renderTimeline({ currentStage: null });
    expect(screen.queryByTestId('status-current-stage')).not.toBeInTheDocument();
  });

  it('hides the decision-letter download until the decision is ready with a PDF', () => {
    const onDownloadLetter = vi.fn();
    renderTimeline({ decisionReady: false, pdfAvailable: false }, { onDownloadLetter });
    expect(screen.queryByTestId('download-decision-letter')).not.toBeInTheDocument();

    renderTimeline({ decisionReady: true, pdfAvailable: true }, { onDownloadLetter });
    fireEvent.click(screen.getByTestId('download-decision-letter'));
    expect(onDownloadLetter).toHaveBeenCalledTimes(1);
  });

  it('disables the download button and shows progress while preparing', () => {
    const onDownloadLetter = vi.fn();
    renderTimeline(
      { decisionReady: true, pdfAvailable: true },
      { onDownloadLetter, downloading: true }
    );
    const button = screen.getByTestId('download-decision-letter');
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent('Preparing…');
  });

  it('renders only masked payload fields — no email, full name, or policy details', () => {
    // The portal response never carries these fields; passing them anyway
    // proves the component cannot leak what it never reads.
    renderTimeline({
      holderName: 'Samuel Riviera',
      email: 'sam@example.com',
      phone: '+1-555-0100',
      policyNumber: 'AUTO-2024-001847',
    });

    expect(screen.queryByText('Samuel Riviera')).not.toBeInTheDocument();
    expect(screen.queryByText('sam@example.com')).not.toBeInTheDocument();
    expect(screen.queryByText('+1-555-0100')).not.toBeInTheDocument();
    expect(screen.queryByText('AUTO-2024-001847')).not.toBeInTheDocument();
  });

  // F14 reopen flow: the chip swaps to "Being reviewed again", the status note
  // tells the customer their claim is under review again, and a conditional
  // reopened milestone completes the timeline.
  it('renders the reopened status with the being-reviewed-again note', () => {
    renderTimeline({
      status: 'reopened',
      statusLabel: 'Being reviewed again',
      statusNote: "Your claim is being reviewed again — we'll keep you updated as it progresses.",
      currentStage: null,
      milestones: [
        ...basePayload.milestones,
        {
          key: 'reopened',
          label: 'Claim reopened — under review again',
          done: true,
          at: '2026-09-18T11:00:00+00:00',
        },
      ],
    });

    expect(screen.getByTestId('status-badge')).toHaveTextContent('Being reviewed again');
    expect(screen.getByTestId('status-note')).toHaveTextContent(
      /your claim is being reviewed again/i
    );
    expect(screen.getByTestId('milestone-reopened')).toBeInTheDocument();
    expect(screen.getByText('3 of 5 milestones complete')).toBeInTheDocument();
  });

  it('shows no status note or reopened milestone for claims that never reopened', () => {
    renderTimeline();
    expect(screen.queryByTestId('status-note')).not.toBeInTheDocument();
    expect(screen.queryByTestId('milestone-reopened')).not.toBeInTheDocument();
  });

  it('renders the current decision outcome so a second decision is visible', () => {
    const { unmount } = renderTimeline({ decisionOutcome: 'approved' });
    expect(screen.getByTestId('decision-outcome')).toHaveTextContent('approved');
    unmount();

    renderTimeline({ decisionOutcome: 'rejected' });
    expect(screen.getByTestId('decision-outcome')).toHaveTextContent('rejected');
  });

  it('omits the decision-outcome row when no decision is on record', () => {
    renderTimeline({ decisionOutcome: null });
    expect(screen.queryByTestId('decision-outcome')).not.toBeInTheDocument();
  });
});

// ============ F7: settlement card on the timeline ============

describe('StatusTimeline settlement card', () => {
  it('renders no settlement card before a settlement is recorded (AC-7.1)', () => {
    // basePayload carries no settlement key — the pre-settlement payload.
    renderTimeline();
    expect(screen.queryByTestId('settlement-card')).not.toBeInTheDocument();
  });

  it('renders the settlement card once the payload carries a settlement (AC-7.1)', () => {
    renderTimeline({
      settlement: { amount: 1150, settledAt: '2026-09-19T10:42:00+00:00' },
    });

    expect(screen.getByTestId('settlement-card')).toBeInTheDocument();
    expect(screen.getByTestId('settlement-amount')).toHaveTextContent('$1,150.00');
  });

  it('completes the final milestone when payout_recorded is done (AC-7.1)', () => {
    // A settled claim has run to the end: every milestone is done.
    renderTimeline({
      milestones: basePayload.milestones.map((m) =>
        m.key === 'decision_ready' || m.key === 'payout_recorded'
          ? { ...m, done: true, at: '2026-09-19T10:42:00+00:00' }
          : m
      ),
    });

    expect(screen.getByTestId('milestone-payout_recorded')).toBeInTheDocument();
    expect(screen.getByText('4 of 4 milestones complete')).toBeInTheDocument();
  });
});
