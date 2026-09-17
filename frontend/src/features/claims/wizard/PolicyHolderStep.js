// Step 2 — Policy holder: live policy lookup + claimant identity fields.
import { Field, TextInput, Select } from './fields';
import PolicyLookupField from './PolicyLookupField';

const ROLES = ['policyholder', 'spouse', 'family_member', 'dependent', 'authorized_representative'];

export default function PolicyHolderStep({ draft, update, errors, lookup }) {
  return (
    <div className="space-y-5" data-testid="wizard-step-1">
      <PolicyLookupField
        value={draft.policyNumber}
        onChange={(v) => update({ policyNumber: v })}
        error={errors.policyNumber}
        lookup={lookup}
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <Field label="Full Name" required error={errors.holderName}>
          <TextInput
            value={draft.holderName}
            onChange={(e) => update({ holderName: e.target.value })}
            placeholder="As it appears on the policy"
            error={errors.holderName}
            testid="holder-name"
          />
        </Field>
        <Field label="Email" required error={errors.holderEmail}>
          <TextInput
            type="email"
            value={draft.holderEmail}
            onChange={(e) => update({ holderEmail: e.target.value })}
            placeholder="you@example.com"
            error={errors.holderEmail}
            testid="holder-email"
          />
        </Field>
      </div>

      <Field label="Your Role in the Incident" required error={errors.incidentRole}>
        <Select
          value={draft.incidentRole}
          onChange={(e) => update({ incidentRole: e.target.value })}
          error={errors.incidentRole}
          testid="incident-role"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>{r.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</option>
          ))}
        </Select>
      </Field>
    </div>
  );
}
