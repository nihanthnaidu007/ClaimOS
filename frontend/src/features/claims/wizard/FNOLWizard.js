// FNOL wizard orchestrator: 4 steps (Incident → Policy Holder → Coverage →
// Review), per-step validation gates, draft autosave/resume, live policy
// lookup, and the final submit handoff. Step navigation and the debounced
// policy lookup live here; the draft model and validators stay pure.
import { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, RotateCcw, X, Save } from 'lucide-react';
import { usePolicyLookup } from '@/lib/queries';
import useWizardDraft from './useWizardDraft';
import useDebounced from './useDebounced';
import { validateIncident, validateHolder } from './validation';
import IncidentStep from './IncidentStep';
import PolicyHolderStep from './PolicyHolderStep';
import CoverageStep from './CoverageStep';
import ReviewStep from './ReviewStep';

const STEPS = ['Incident', 'Policy Holder', 'Coverage', 'Review'];

export default function FNOLWizard({ onSubmit }) {
  const wizard = useWizardDraft();
  const [step, setStep] = useState(0);
  const [files, setFiles] = useState([]);
  const [touchedSteps, setTouchedSteps] = useState({});
  const [submitError, setSubmitError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const { draft, update, restored, resumeDraft, discard, dismissResume, finalize } = wizard;

  // One debounced lookup shared by validation, the holder field, and coverage.
  const policyNum = (draft.policyNumber || '').trim();
  const debouncedPolicy = useDebounced(policyNum, 500);
  const lookup = usePolicyLookup(debouncedPolicy.length >= 4 ? debouncedPolicy : null);

  // Per-step error maps (recomputed, never stored — validation is pure).
  const incidentErrors = useMemo(() => validateIncident(draft), [draft]);
  const holderErrors = useMemo(() => validateHolder(draft, lookup), [draft, lookup]);
  const stepErrors = [incidentErrors, holderErrors, {}, {}];
  const errors = touchedSteps[step] ? (stepErrors[step] || {}) : {};

  const markTouched = (s) => setTouchedSteps((t) => ({ ...t, [s]: true }));

  const next = () => {
    if (Object.keys(stepErrors[step] || {}).length > 0) { markTouched(step); return; }
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const back = () => {
    setStep((s) => Math.max(s - 1, 0));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const jumpTo = (target) => {
    setStep(target);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const submit = async () => {
    const allBlocking = [incidentErrors, holderErrors].flatMap((m) => Object.keys(m));
    if (allBlocking.length > 0) { markTouched(step); return; }
    setSubmitError('');
    setSubmitting(true);
    try {
      await onSubmit({ ...draft, estimatedCost: Number(draft.estimatedCost) }, files);
      await finalize();
      setFiles([]);
    } catch (err) {
      setSubmitError(err?.response?.data?.detail || err?.message || 'Submission failed — try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const addFiles = (incoming) => {
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...incoming.filter((f) => !seen.has(`${f.name}:${f.size}`))];
    });
  };

  if (!restored) {
    return (
      <div className="text-center py-12 text-xs font-mono text-[#4a5568]" data-testid="wizard-restoring">
        Restoring draft…
      </div>
    );
  }

  return (
    <div data-testid="fnol-wizard">
      {/* Resume banner */}
      {resumeDraft && (
        <div
          className="mb-6 bg-[#3b82f6]/5 border border-[#3b82f6]/30 rounded-sm p-4 flex items-center justify-between"
          data-testid="draft-resume-banner"
        >
          <div className="flex items-center gap-2 text-xs font-mono text-[#7dd3fc]">
            <Save className="w-3.5 h-3.5" />
            Draft restored from {new Date(resumeDraft.savedAt || Date.now()).toLocaleString()} — review and continue.
          </div>
          <div className="flex items-center gap-2">
            <button
              data-testid="draft-discard-btn"
              onClick={() => { discard(); dismissResume(); }}
              className="text-xs font-mono text-[#ef4444] hover:text-[#f87171] flex items-center gap-1"
            >
              <RotateCcw className="w-3 h-3" /> Start over
            </button>
            <button
              data-testid="draft-dismiss-btn"
              onClick={dismissResume}
              className="text-[#4a5568] hover:text-[#8892a4]"
              aria-label="Dismiss"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Stepper header */}
      <div className="flex items-center gap-2 mb-6" data-testid="wizard-stepper">
        {STEPS.map((label, i) => {
          const done = i < step && Object.keys(stepErrors[i] || {}).length === 0;
          const active = i === step;
          return (
            <div key={label} className="flex items-center gap-2 flex-1 last:flex-none">
              <button
                onClick={() => i <= step && jumpTo(i)}
                disabled={i > step}
                className={`flex items-center gap-2 px-3 py-1.5 text-xs font-mono uppercase tracking-wider transition-colors duration-200 ${
                  active ? 'text-[#3b82f6] border-b-2 border-[#3b82f6]' :
                  done ? 'text-[#10b981]' :
                  'text-[#4a5568]'
                }`}
                data-testid={`stepper-${i}`}
              >
                <span className={`w-5 h-5 rounded-full border flex items-center justify-center text-[10px] ${
                  active ? 'border-[#3b82f6] text-[#3b82f6]' :
                  done ? 'bg-[#10b981] border-[#10b981] text-[#0a0c12]' :
                  'border-[#232b3d]'
                }`}>
                  {done ? '✓' : i + 1}
                </span>
                {label}
              </button>
              {i < STEPS.length - 1 && <div className="flex-1 h-px bg-[#1a1f2e]" />}
            </div>
          );
        })}
      </div>

      {/* Active step body */}
      {step === 0 && <IncidentStep draft={draft} update={update} errors={errors} files={files} onFiles={addFiles} />}
      {step === 1 && (
        <PolicyHolderStep draft={draft} update={update} errors={errors} lookup={lookup} />
      )}
      {step === 2 && <CoverageStep draft={draft} lookup={lookup} />}
      {step === 3 && (
        <ReviewStep
          draft={draft}
          errors={{ ...incidentErrors, ...holderErrors }}
          files={files}
          onSubmit={submit}
          submitting={submitting}
          submitError={submitError}
          onEdit={jumpTo}
        />
      )}

      {/* Footer nav */}
      <div className="flex items-center justify-between mt-8 pt-4 border-t border-[#1a1f2e]">
        <button
          data-testid="wizard-back-btn"
          onClick={back}
          disabled={step === 0}
          className="flex items-center gap-1 text-sm font-mono text-[#8892a4] hover:text-[#e2e8f0] disabled:opacity-30 disabled:cursor-not-allowed transition-colors duration-200"
        >
          <ChevronLeft className="w-4 h-4" /> Back
        </button>
        {step < STEPS.length - 1 ? (
          <button
            data-testid="wizard-next-btn"
            onClick={next}
            className="flex items-center gap-1 bg-[#3b82f6] hover:bg-[#2563eb] text-white font-semibold rounded-none px-6 py-2.5 text-sm transition-colors duration-200"
            style={{ fontFamily: 'Space Grotesk' }}
          >
            Next <ChevronRight className="w-4 h-4" />
          </button>
        ) : <span />}
      </div>
    </div>
  );
}
