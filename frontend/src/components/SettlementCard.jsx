// F7 settlement visibility: renders the customer settlement card from the
// masked portal payload's `settlement` object — which the backend projection
// builds field-by-field from the stored settlement record. Only fields that
// exist on the record render (amount, recorded date); payment-method, fee,
// and reference data have no path into the payload, and this component reads
// nothing but the two fields below even if a future payload carried them.
// All copy comes from frontend/src/portalCopy.js — nothing hardcoded inline.
import { BadgeCheck } from 'lucide-react';

import { formatCurrency, formatDateTime } from '@/lib/workbench';
import { SETTLEMENT_CARD_COPY } from '../portalCopy';

const hasAmount = (value) => typeof value === 'number' && !Number.isNaN(value);

const hasDate = (value) =>
  typeof value === 'string' && value.trim() !== '';

export function SettlementCard({ settlement }) {
  if (!settlement || typeof settlement !== 'object') {
    // No settlement recorded (the payload omits the key entirely) — no card.
    return null;
  }

  const showAmount = hasAmount(settlement.amount);
  const showDate = hasDate(settlement.settledAt);

  return (
    <section
      data-testid="settlement-card"
      aria-labelledby="settlement-card-heading"
      className="mt-6 border border-[#10b981]/30 bg-[#10b981]/5 rounded-sm p-6"
    >
      <h2
        id="settlement-card-heading"
        className="text-xs uppercase tracking-wider text-[#8892a4] font-mono flex items-center gap-2"
      >
        <BadgeCheck className="w-4 h-4 text-[#10b981]" />
        {SETTLEMENT_CARD_COPY.heading}
      </h2>
      {showAmount && (
        <p
          data-testid="settlement-amount"
          className="mt-3 text-2xl font-semibold text-[#e2e8f0] font-mono"
        >
          {formatCurrency(settlement.amount)}
        </p>
      )}
      {showDate && (
        <p
          data-testid="settlement-recorded-date"
          className="mt-1 text-xs text-[#8892a4] font-mono"
        >
          Recorded {formatDateTime(settlement.settledAt)}
        </p>
      )}
      <p
        data-testid="settlement-payment-timing"
        className="mt-3 text-sm text-[#8892a4]"
      >
        {SETTLEMENT_CARD_COPY.paymentTiming}
      </p>
    </section>
  );
}

export default SettlementCard;
