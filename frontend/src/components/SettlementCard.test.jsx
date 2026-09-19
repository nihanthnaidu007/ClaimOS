// SettlementCard component test — F7: the card mounts only once a settlement
// is recorded, renders only fields that exist on the record (amount, recorded
// date), and never invents payment-method or fee data even if a drifted
// payload carried it.
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { SettlementCard } from './SettlementCard';
import { SETTLEMENT_CARD_COPY } from '../portalCopy';

const recordedAt = '2026-09-19T10:42:00+00:00';

const fullSettlement = {
  amount: 1150,
  settledAt: recordedAt,
};

describe('SettlementCard', () => {
  it('renders the recorded amount, date, and payment-timing next step', () => {
    render(<SettlementCard settlement={fullSettlement} />);

    expect(screen.getByTestId('settlement-card')).toBeInTheDocument();
    expect(screen.getByTestId('settlement-amount')).toHaveTextContent('$1,150.00');
    expect(screen.getByTestId('settlement-recorded-date')).toHaveTextContent(/Recorded/);
    expect(screen.getByTestId('settlement-payment-timing')).toHaveTextContent(
      SETTLEMENT_CARD_COPY.paymentTiming
    );
  });

  it('renders copy from the editable constant, not hardcoded strings', () => {
    render(<SettlementCard settlement={fullSettlement} />);

    expect(screen.getByText(SETTLEMENT_CARD_COPY.heading)).toBeInTheDocument();
  });

  it('renders nothing before a settlement is recorded (absence branch)', () => {
    const { container } = render(<SettlementCard settlement={undefined} />);

    expect(container).toBeEmptyDOMElement();
  });

  it('renders no amount or date placeholders when the record lacks them (AC-7.2)', () => {
    // A record with no visible fields projects to an empty card body from the
    // backend — and the component renders exactly the fields that exist.
    render(<SettlementCard settlement={{}} />);

    expect(screen.getByTestId('settlement-card')).toBeInTheDocument();
    expect(screen.queryByTestId('settlement-amount')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settlement-recorded-date')).not.toBeInTheDocument();
    expect(screen.getByTestId('settlement-payment-timing')).toBeInTheDocument();
  });

  it('never renders payment-method, fee, or reference data even if the payload carried it', () => {
    // Structural deny: these keys are not read, so invented payment data has
    // no path to the surface even if a drifted projection sent them.
    render(
      <SettlementCard
        settlement={{
          ...fullSettlement,
          method: 'bank_transfer',
          reference: 'BNK-2026-000123',
          fee: 12.5,
          recordedBy: 'adjuster@claimos.dev',
        }}
      />
    );

    expect(screen.queryByText(/bank transfer/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/BNK-2026-000123/)).not.toBeInTheDocument();
    expect(screen.queryByText(/12\.5/)).not.toBeInTheDocument();
    expect(screen.queryByText(/adjuster@claimos\.dev/)).not.toBeInTheDocument();
  });
});
