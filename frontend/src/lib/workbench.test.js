// Unit tests for the pure workbench presentation helpers.
import { describe, it, expect } from 'vitest';
import {
  SLA_STATES,
  isReviewable,
  formatHours,
  severityPresentation,
  slaBadgeText,
  slaPresentation,
  statusClassName,
} from './workbench';

describe('workbench helpers', () => {
  it('exposes the three SLA states the spec defines', () => {
    expect(SLA_STATES).toEqual(['ok', 'at_risk', 'breached']);
  });

  it('maps SLA states to distinct presentations with red reserved for breaches', () => {
    const breached = slaPresentation({ state: 'breached' });
    const atRisk = slaPresentation({ state: 'at_risk' });
    const ok = slaPresentation({ state: 'ok' });

    expect(breached.className).toContain('#ef4444'); // red
    expect(atRisk.className).toContain('#f59e0b'); // amber
    expect(ok.className).toContain('#10b981'); // green
    expect(breached.className).not.toEqual(atRisk.className);
  });

  it('falls back to the on-track presentation for unknown or missing state', () => {
    expect(slaPresentation(undefined)).toEqual(slaPresentation({ state: 'ok' }));
    expect(slaPresentation({ state: 'nonsense' }).label).toBe('ON TRACK');
  });

  it('renders badge text as hours-left, or hours-over once breached', () => {
    expect(slaBadgeText({ state: 'ok', breached: false, hoursRemaining: 12.34 })).toBe(
      'ON TRACK · 12.3h left'
    );
    expect(slaBadgeText({ state: 'breached', breached: true, hoursRemaining: -3.21 })).toBe(
      'SLA BREACHED · 3.2h over'
    );
    expect(slaBadgeText(undefined)).toBe('');
  });

  it('formats hour values into minutes, hours, and days', () => {
    expect(formatHours(0.25)).toBe('15m');
    expect(formatHours(5.5)).toBe('5.5h');
    expect(formatHours(96)).toBe('4d');
    expect(formatHours(undefined)).toBe('—');
    expect(formatHours('not-a-number')).toBe('—');
  });

  it('presents elevated severity distinctly from low', () => {
    expect(severityPresentation('elevated').label).toBe('ELEVATED');
    expect(severityPresentation('low').label).toBe('LOW');
    expect(severityPresentation('unknown').label).toBe('LOW');
  });

  it('gives every status a background class and never throws on unknown statuses', () => {
    expect(statusClassName('escalated')).toMatch(/^bg-/);
    expect(statusClassName('mystery')).toMatch(/^bg-/);
  });

  it('mirrors the backend reviewable-status set', () => {
    expect(isReviewable('escalated')).toBe(true);
    expect(isReviewable('pending')).toBe(true);
    expect(isReviewable('under_review')).toBe(true);
    expect(isReviewable('approved')).toBe(false);
    expect(isReviewable('overridden')).toBe(false);
  });
});
