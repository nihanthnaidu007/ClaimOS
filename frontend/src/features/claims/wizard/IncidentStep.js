// Step 1 — Incident: type, date, narrative, estimated amount, documents.
import { Field, TextInput, TextArea, Select, FileInput } from './fields';
import { INCIDENT_TYPES, formatDollars } from '../constants';

export default function IncidentStep({ draft, update, errors, files, onFiles }) {
  const setType = (v) => update({ incidentType: v });
  return (
    <div className="space-y-5" data-testid="wizard-step-0">
      <Field label="Incident Type" required error={errors.incidentType}>
        <Select
          value={draft.incidentType}
          onChange={(e) => setType(e.target.value)}
          error={errors.incidentType}
          testid="incident-type"
        >
          <option value="">Select incident type…</option>
          {INCIDENT_TYPES.map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</option>
          ))}
        </Select>
      </Field>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <Field label="Date of Incident" required error={errors.incidentDate}>
          <TextInput
            type="date"
            value={draft.incidentDate}
            max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => update({ incidentDate: e.target.value })}
            error={errors.incidentDate}
            testid="incident-date"
          />
        </Field>
        <Field
          label="Estimated Amount"
          required
          error={errors.estimatedCost}
          hint={Number(draft.estimatedCost) > 0 ? `Claiming ${formatDollars(Number(draft.estimatedCost))}` : ''}
        >
          <TextInput
            type="number"
            min="0"
            step="0.01"
            value={draft.estimatedCost}
            onChange={(e) => update({ estimatedCost: e.target.value })}
            placeholder="0.00"
            error={errors.estimatedCost}
            testid="estimated-cost"
          />
        </Field>
      </div>

      <Field
        label="What happened?"
        required
        error={errors.description}
        hint={`${(draft.description || '').trim().length}/20 characters minimum`}
      >
        <TextArea
          value={draft.description}
          onChange={(e) => update({ description: e.target.value })}
          placeholder="Describe the incident in your own words — what happened, when, where, and any parties involved."
          error={errors.description}
          testid="incident-description"
        />
      </Field>

      <Field label="Supporting Documents">
        <FileInput files={files} onAdd={onFiles} />
      </Field>
    </div>
  );
}
