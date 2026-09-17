// Live policy-number lookup field (presentational): renders the verdict chip
// and coverage facts from the lookup query the wizard owns. Feedback must be
// inline (UX bar) — the user never waits for a submit round-trip to learn a
// number is dead.
import { CheckCircle2, XCircle, Loader2, Shield } from 'lucide-react';
import { formatDollars } from '../constants';

const VERDICT_META = {
  pending: { text: 'Checking policy…', cls: 'text-[#f59e0b]' },
  error: { text: 'Lookup failed — will retry', cls: 'text-[#ef4444]' },
  notfound: { text: 'No policy found', cls: 'text-[#ef4444]' },
  inactive: { text: 'Policy is not active', cls: 'text-[#ef4444]' },
};

function verdictFor(lookup, value) {
  if (!value.trim()) return null;
  if (lookup?.isPending) return 'pending';
  if (lookup?.isError) return 'error';
  if (lookup?.data?.found === false) return 'notfound';
  if (lookup?.data?.found && !lookup.data.active) return 'inactive';
  if (lookup?.data?.found) return 'found';
  return null;
}

export default function PolicyLookupField({ value, onChange, error, lookup }) {
  const verdict = verdictFor(lookup, value);
  const meta = verdict && VERDICT_META[verdict];
  const policy = lookup?.data;

  return (
    <div>
      <label className="block text-xs font-mono text-[#8892a4] uppercase tracking-wider mb-2">
        Policy Number *
      </label>
      <div className="relative">
        <input
          type="text"
          data-testid="policy-number-input"
          value={value}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          placeholder="POL-2024-XXXX"
          className={`w-full bg-[#0a0c12] border rounded-none px-4 py-3 text-sm text-[#e2e8f0] font-mono placeholder-[#2d3548] focus:outline-none focus:border-[#3b82f6] transition-colors duration-200 pr-10 ${
            error || verdict === 'notfound' || verdict === 'inactive' ? 'border-[#ef4444]' : 'border-[#232b3d]'
          }`}
        />
        <span className="absolute right-3 top-1/2 -translate-y-1/2">
          {verdict === 'pending' && <Loader2 className="w-4 h-4 text-[#f59e0b] animate-spin" />}
          {verdict === 'found' && <CheckCircle2 className="w-4 h-4 text-[#10b981]" />}
          {(verdict === 'notfound' || verdict === 'inactive' || verdict === 'error' || error) && (
            <XCircle className="w-4 h-4 text-[#ef4444]" />
          )}
        </span>
      </div>

      {error && <p className="text-xs font-mono text-[#ef4444] mt-1.5" data-testid="policy-number-error">{error}</p>}
      {!error && meta && (
        <p className={`text-xs font-mono mt-1.5 ${meta.cls}`} data-testid="policy-number-feedback">{meta.text}</p>
      )}

      {/* Inline coverage facts once the policy resolves */}
      {verdict === 'found' && policy && (
        <div
          className="mt-3 bg-[#10b981]/5 border border-[#10b981]/30 rounded-sm p-3 grid grid-cols-2 md:grid-cols-4 gap-2 text-xs font-mono"
          data-testid="policy-found-panel"
        >
          <Fact label="Holder" value={policy.holderName} />
          <Fact label="Coverage" value={policy.coverageLimit ? formatDollars(policy.coverageLimit) : '—'} />
          <Fact label="Deductible" value={policy.deductible ? formatDollars(policy.deductible) : '—'} />
          <Fact
            label="Status"
            value={policy.active ? 'ACTIVE' : 'INACTIVE'}
            valueCls={policy.active ? 'text-[#10b981]' : 'text-[#ef4444]'}
          />
        </div>
      )}

      {verdict === 'found' && !policy?.active && (
        <p className="text-xs font-mono text-[#f59e0b] mt-2 flex items-center gap-1.5">
          <Shield className="w-3 h-3" /> Claims on inactive policies will be escalated, not auto-processed.
        </p>
      )}
    </div>
  );
}

function Fact({ label, value, valueCls = 'text-[#e2e8f0]' }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[#4a5568]">{label}</div>
      <div className={`${valueCls} truncate`}>{value || '—'}</div>
    </div>
  );
}
