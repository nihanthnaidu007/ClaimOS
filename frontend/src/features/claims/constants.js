// Shared claim-domain constants and formatting helpers.

export const INCIDENT_TYPES = [
  'accident', 'theft', 'vandalism', 'weather_damage', 'fire', 'flood',
  'earthquake', 'wind_damage', 'hit_and_run', 'total_loss',
  'accidental_damage', 'malfunction', 'hospitalization', 'surgery',
  'emergency', 'specialist_visit', 'dental', 'vision',
];

export const AGENT_META = {
  INTAKE_AGENT: { index: 1, label: 'Intake & Validation', desc: 'Validating claim fields and normalizing data', icon: '01' },
  POLICY_AGENT: { index: 2, label: 'Policy Verification', desc: 'Querying policy database for coverage verification', icon: '02' },
  DOCUMENT_AGENT: { index: 3, label: 'Document Analysis', desc: 'Analyzing claim documents for evidence and consistency', icon: '03' },
  ELIGIBILITY_AGENT: { index: 4, label: 'Eligibility & Risk', desc: 'Calculating risk score and eligibility verdict', icon: '04' },
  DECISION_AGENT: { index: 5, label: 'Decision & Communication', desc: 'Issuing final verdict and drafting communication', icon: '05' },
};

export const AGENT_ORDER = [
  'INTAKE_AGENT',
  'POLICY_AGENT',
  'DOCUMENT_AGENT',
  'ELIGIBILITY_AGENT',
  'DECISION_AGENT',
];

export function formatDollars(n) {
  if (n == null || Number.isNaN(Number(n))) return '$0.00';
  return `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
