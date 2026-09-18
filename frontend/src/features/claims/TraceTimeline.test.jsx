// TraceTimeline tests — durable per-agent trace rendering: canonical agent
// ordering, status/confidence display, expandable tool calls, run history,
// the persisted event stream, and the explicit empty state.
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import TraceTimeline from './TraceTimeline';

// Logs are deliberately OUT of canonical order to prove the timeline sorts.
// Status values use the worker's vocabulary: running → done | error.
const LOGS = [
  {
    agent: 'POLICY_AGENT',
    status: 'done',
    confidence: 0.92,
    duration: 812,
    output: { policy: 'POL-2024-001847', active: true },
  },
  {
    agent: 'FRAUD_AGENT',
    status: 'done',
    confidence: 0.88,
    duration: 205,
    output: { flags: 0, duplicates: 0 },
  },
  {
    agent: 'INTAKE_AGENT',
    status: 'done',
    confidence: 0.99,
    duration: 120,
    output: { fields: 7 },
    toolsCalled: ['validate_claim_fields', 'normalize_dates'],
  },
  {
    agent: 'DECISION_AGENT',
    status: 'error',
    confidence: 0.4,
    duration: 300,
    error: 'LLM adapter unavailable after 3 attempts',
    output: {},
  },
];

const RUNS = [
  { attempt: 1, status: 'failed', failureReason: 'llm_adapter_error' },
  { attempt: 2, status: 'running' },
];

const EVENTS = [
  { seq: 1, event: 'agent_start', data: { agent: 'INTAKE_AGENT' }, createdAt: '2026-09-17T10:00:00Z' },
  { seq: 2, event: 'agent_complete', data: { agent: 'INTAKE_AGENT', confidence: 0.99 }, createdAt: '2026-09-17T10:00:01Z' },
  { seq: 3, event: 'run_finalized', data: { status: 'failed' }, createdAt: '2026-09-17T10:00:02Z' },
];

function fixture(overrides = {}) {
  return { agentLogs: LOGS, runs: RUNS, events: EVENTS, ...overrides };
}

describe('TraceTimeline', () => {
  it('renders agent cards in canonical AGENT_ORDER regardless of input order', () => {
    render(<TraceTimeline trace={fixture()} />);
    const cards = screen.getAllByTestId(/^trace-agent-[A-Z]/);
    const ids = cards.map((el) => el.dataset.testid);
    expect(ids).toEqual([
      'trace-agent-INTAKE_AGENT',
      'trace-agent-POLICY_AGENT',
      'trace-agent-FRAUD_AGENT',
      'trace-agent-DECISION_AGENT',
    ]);
  });

  it('surfaces confidence, status, and per-agent errors', () => {
    render(<TraceTimeline trace={fixture()} />);

    expect(screen.getAllByTestId('trace-confidence')).toHaveLength(4);
    expect(screen.getByText('confidence 99%')).toBeInTheDocument();
    expect(screen.getByText('confidence 92%')).toBeInTheDocument();
    expect(screen.getByTestId('trace-agent-error')).toHaveTextContent(
      'LLM adapter unavailable after 3 attempts'
    );
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('expands tool calls on demand', () => {
    render(<TraceTimeline trace={fixture()} />);

    const intake = screen.getByTestId('trace-agent-INTAKE_AGENT');
    expect(within(intake).getByTestId('tool-calls-2')).toBeInTheDocument();
    expect(within(intake).queryByText('validate_claim_fields')).not.toBeInTheDocument();

    fireEvent.click(within(intake).getByTestId('tool-calls-toggle'));
    expect(within(intake).getByText('validate_claim_fields')).toBeInTheDocument();
    expect(within(intake).getByText('normalize_dates')).toBeInTheDocument();
  });

  it('renders run history chips with failure reasons', () => {
    render(<TraceTimeline trace={fixture()} />);

    const runs = screen.getByTestId('trace-runs');
    expect(within(runs).getByText(/run #1: failed — llm_adapter_error/)).toBeInTheDocument();
    expect(within(runs).getByText(/run #2: running/)).toBeInTheDocument();
  });

  it('renders the durable event stream in seq order', () => {
    render(<TraceTimeline trace={fixture()} />);

    const list = screen.getByTestId('trace-events');
    const rows = within(list).getAllByRole('listitem');
    expect(rows).toHaveLength(3);
    expect(within(list).getByText('agent_start')).toBeInTheDocument();
    expect(within(list).getByText('run_finalized')).toBeInTheDocument();
  });

  it('shows an explicit empty state when no trace exists', () => {
    render(<TraceTimeline trace={{ agentLogs: [], events: [], runs: [] }} />);
    expect(screen.getByTestId('trace-empty')).toBeInTheDocument();
  });

  it('does not crash on a missing trace prop', () => {
    render(<TraceTimeline trace={null} />);
    expect(screen.getByTestId('trace-empty')).toBeInTheDocument();
  });
});
