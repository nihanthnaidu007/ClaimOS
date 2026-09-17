// Step 3 — Coverage: what the looked-up policy will and will not cover for
// this claim amount, before the user reviews. Passive by design — the facts
// come from the verified policy record, not user edits. Receives the shared
// lookup query result from the wizard.
import { Shield, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { formatDollars } from '../constants';

export default function CoverageStep({ draft, lookup }) {
  const policy = lookup?.data;
  const cost = Number(draft.estimatedCost) || 0;
  const limit = policy?.coverageLimit || 0;
  const deductible = policy?.deductible || 0;
  const withinLimit = limit > 0 && cost <= limit;

  if (lookup?.isPending || !policy?.found) {
    return (
      <div className="space-y-4" data-testid="wizard-step-2">
        <BlankNotice>
          Coverage details appear once the policy number from the previous step resolves.
        </BlankNotice>
      </div>
    );
  }

  return (
    <div className="space-y-5" data-testid="wizard-step-2">
      <div className="bg-[#0f1218] border border-[#232b3d] rounded-sm p-4">
        <div className="flex items-center gap-2 mb-3">
          <Shield className="w-4 h-4 text-[#7dd3fc]" />
          <span className="text-sm font-semibold text-[#e2e8f0]" style={{ fontFamily: 'Space Grotesk' }}>
            Policy {policy.policyNumber}
          </span>
          <span className={`text-[10px] font-mono px-2 py-0.5 border ${
            policy.active ? 'text-[#10b981] border-[#10b981]/40' : 'text-[#ef4444] border-[#ef4444]/40'
          }`}>
            {policy.active ? 'ACTIVE' : 'INACTIVE'}
          </span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs font-mono">
          <Stat label="Policy Holder" value={policy.holderName} />
          <Stat label="Coverage Limit" value={formatDollars(limit)} />
          <Stat label="Deductible" value={formatDollars(deductible)} />
          <Stat label="Effective" value={policy.effectiveDate || '—'} />
        </div>
      </div>

      <div className="bg-[#0f1218] border border-[#232b3d] rounded-sm p-4">
        <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono mb-3">Coverage Math for This Claim</div>
        <ul className="space-y-2 text-xs font-mono">
          <MathRow
            ok={policy.active}
            text={policy.active ? 'Policy is active — claim can be adjudicated automatically.' : 'Policy is inactive — your claim will be escalated for manual review.'}
          />
          <MathRow
            ok={withinLimit}
            text={withinLimit
              ? `Claimed ${formatDollars(cost)} is within the ${formatDollars(limit)} coverage limit.`
              : `Claimed ${formatDollars(cost)} exceeds the ${formatDollars(limit)} coverage limit — payout will be capped or escalated.`}
          />
          <MathRow
            ok
            text={deductible > 0
              ? `A ${formatDollars(deductible)} deductible applies before payout.`
              : 'No deductible applies to this policy.'}
          />
        </ul>
      </div>

      {!policy.active && (
        <Notice tone="warn" icon={AlertTriangle}>
          Submitting against an inactive policy skips straight-through processing — a human adjuster will review it.
        </Notice>
      )}
      {policy.active && withinLimit && (
        <Notice tone="ok" icon={CheckCircle2}>
          This claim looks eligible for straight-through processing after document review.
        </Notice>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[#4a5568]">{label}</div>
      <div className="text-[#e2e8f0] mt-0.5 truncate">{value || '—'}</div>
    </div>
  );
}

function MathRow({ ok, text }) {
  return (
    <li className="flex items-start gap-2">
      {ok
        ? <CheckCircle2 className="w-3.5 h-3.5 text-[#10b981] mt-0.5 flex-shrink-0" />
        : <AlertTriangle className="w-3.5 h-3.5 text-[#f59e0b] mt-0.5 flex-shrink-0" />}
      <span className="text-[#8892a4]">{text}</span>
    </li>
  );
}

function Notice({ tone, icon: Icon, children }) {
  const cls = tone === 'ok'
    ? 'bg-[#10b981]/5 border-[#10b981]/30 text-[#10b981]'
    : 'bg-[#f59e0b]/5 border-[#f59e0b]/30 text-[#f59e0b]';
  return (
    <div className={`border rounded-sm p-3 text-xs font-mono flex items-start gap-2 ${cls}`}>
      <Icon className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
      <span>{children}</span>
    </div>
  );
}

function BlankNotice({ children }) {
  return (
    <div className="border border-dashed border-[#232b3d] rounded-sm p-6 text-center text-xs font-mono text-[#4a5568]">
      {children}
    </div>
  );
}
