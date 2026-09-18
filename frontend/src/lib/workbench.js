// Pure workbench presentation helpers: SLA badge styling, severity pills, and
// formatters. No React, no I/O — trivially unit-testable (see workbench.test.js).

export const SLA_STATES = ['ok', 'at_risk', 'breached'];

const SLA_PRESENTATION = {
  breached: {
    label: 'SLA BREACHED',
    icon: 'octagon-alert',
    className: 'bg-[#ef4444]/15 text-[#ef4444] border-[#ef4444]/40',
    dot: 'bg-[#ef4444]',
  },
  at_risk: {
    label: 'AT RISK',
    icon: 'timer',
    className: 'bg-[#f59e0b]/15 text-[#f59e0b] border-[#f59e0b]/40',
    dot: 'bg-[#f59e0b]',
  },
  ok: {
    label: 'ON TRACK',
    icon: 'check-circle',
    className: 'bg-[#10b981]/15 text-[#10b981] border-[#10b981]/40',
    dot: 'bg-[#10b981]',
  },
};

export function slaPresentation(sla) {
  return SLA_PRESENTATION[sla?.state] ?? SLA_PRESENTATION.ok;
}

export function slaBadgeText(sla) {
  if (!sla) return '';
  const hours = sla.breached
    ? `${Math.abs(sla.hoursRemaining).toFixed(1)}h over`
    : `${sla.hoursRemaining.toFixed(1)}h left`;
  return `${slaPresentation(sla).label} · ${hours}`;
}

const SEVERITY_PRESENTATION = {
  elevated: {
    label: 'ELEVATED',
    className: 'bg-[#f59e0b]/15 text-[#f59e0b] border-[#f59e0b]/40',
  },
  low: {
    label: 'LOW',
    className: 'bg-[#3b82f6]/15 text-[#7cb0ff] border-[#3b82f6]/40',
  },
};

export function severityPresentation(severity) {
  return SEVERITY_PRESENTATION[severity] ?? SEVERITY_PRESENTATION.low;
}

const STATUS_PRESENTATION = {
  escalated: 'bg-[#f59e0b]/15 text-[#f59e0b] border-[#f59e0b]/40',
  pending: 'bg-[#8b96ab]/15 text-[#8b96ab] border-[#8b96ab]/40',
  under_review: 'bg-[#7c3aed]/15 text-[#b79df5] border-[#7c3aed]/40',
  auto_approved: 'bg-[#10b981]/15 text-[#10b981] border-[#10b981]/40',
  approved: 'bg-[#10b981]/15 text-[#10b981] border-[#10b981]/40',
  rejected: 'bg-[#ef4444]/15 text-[#ef4444] border-[#ef4444]/40',
  overridden: 'bg-[#7c3aed]/15 text-[#b79df5] border-[#7c3aed]/40',
};

export function statusClassName(status) {
  return STATUS_PRESENTATION[status] ?? 'bg-[#8b96ab]/15 text-[#8b96ab] border-[#8b96ab]/40';
}

export function formatCurrency(value) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format(Number(value ?? 0));
}

export function formatHours(hours) {
  if (hours == null || Number.isNaN(Number(hours))) return '—';
  const value = Number(hours);
  if (value < 1) return `${Math.round(value * 60)}m`;
  if (value < 48) return `${value.toFixed(1)}h`;
  return `${Math.round(value / 24)}d`;
}

export function formatDateTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatDuration(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// Statuses a claim can be overridden from — mirrors backend REVIEWABLE_STATUSES.
export const REVIEWABLE_STATUSES = ['escalated', 'pending', 'under_review'];

export function isReviewable(status) {
  return REVIEWABLE_STATUSES.includes(status);
}

// ---- Saved views (F12): filter-bar state <-> stored preset ----

// UI filter state -> the wire preset saved in a view. Empty filters are
// dropped so a view stores only what the adjuster actually set.
export function filtersToViewPreset(filters) {
  const preset = {};
  for (const [key, value] of Object.entries(filters)) {
    if (value === '' || value == null) continue;
    if (key === 'minAgeHours') preset.min_age_hours = Number(value);
    else if (key === 'maxAgeHours') preset.max_age_hours = Number(value);
    else preset[key] = value;
  }
  return preset;
}

// Inverse: a stored preset -> full filter-bar state with the queue's initial
// defaults for anything the preset omitted.
export function viewPresetToFilters(preset) {
  return {
    status: preset.status ?? '',
    severity: preset.severity ?? '',
    minAgeHours: preset.min_age_hours ?? '',
    maxAgeHours: preset.max_age_hours ?? '',
    sort: preset.sort ?? 'age',
  };
}
