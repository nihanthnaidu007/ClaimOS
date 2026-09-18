// PipelineBoard tests — regression guard for the policy-agent tool I/O crash:
// the backend records tool output as structured objects ({found, data} for
// policyLookup, {count, claims} for claimHistory), and the board must render
// them as human summaries instead of crashing with "Objects are not valid as
// a React child".
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PipelineBoard, {
  formatToolOutput,
  formatPolicyLookupOutput,
  formatClaimHistoryOutput,
} from './PipelineBoard';

// Mirrors the backend contract (backend/agents.py tool_calls): input is
// JSON-stringified by normalizeToolCalls at ingestion, output stays an object.
const POLICY_LOOKUP_FOUND = {
  tool: 'policyLookup',
  input: '{"policy_number": "AUTO-2024-001847"}',
  output: {
    found: true,
    data: {
      policy_number: 'AUTO-2024-001847',
      holder_name: 'Sarah Chen',
      coverage_limit: 50000,
      status: 'active',
    },
  },
  duration_ms: 12,
};

const POLICY_LOOKUP_MISSING = {
  tool: 'policyLookup',
  input: '{"policy_number": "GONE-0000"}',
  output: { found: false, data: null },
  duration_ms: 8,
};

const CLAIM_HISTORY = {
  tool: 'claimHistory',
  input: '{"policy_number": "AUTO-2024-001847", "window_days": 365}',
  output: { count: 2, claims: [{ id: 'C1' }, { id: 'C2' }] },
  duration_ms: 5,
};

function pipelineFor(toolsCalled) {
  return {
    agents: {
      POLICY_AGENT: {
        status: 'done',
        output: { found: true, status: 'verified' },
        toolsCalled,
        duration: 42,
      },
    },
    status: 'running',
    haltReason: '',
    failureReason: '',
  };
}

function renderBoard(toolsCalled) {
  return render(
    <PipelineBoard claimId="CLM-TEST-001" pipeline={pipelineFor(toolsCalled)} connectionState="live" />,
  );
}

describe('PipelineBoard policy tool output rendering', () => {
  it('renders a found policy lookup as a summary line instead of crashing', () => {
    renderBoard([POLICY_LOOKUP_FOUND, CLAIM_HISTORY]);

    // No crash + the card rendered through its done state.
    expect(screen.getByTestId('agent-card-POLICY_AGENT')).toBeInTheDocument();

    const summary = screen.getByText(/Policy AUTO-2024-001847 · Sarah Chen · coverage \$50,000\.00/);
    expect(summary).toBeInTheDocument();
    expect(screen.getByText(/2 prior claims in the last 12 months/)).toBeInTheDocument();
  });

  it('renders a clean not-found state when found is false', () => {
    renderBoard([POLICY_LOOKUP_MISSING]);

    expect(screen.getByTestId('agent-card-POLICY_AGENT')).toBeInTheDocument();
    expect(screen.getByText(/Not found — no policy record matched this lookup/)).toBeInTheDocument();
  });

  it('renders nothing for an absent tool output (loading/null-safe)', () => {
    const { container } = renderBoard([
      { tool: 'policyLookup', input: '{"policy_number": "PENDING"}', output: null, duration_ms: 1 },
    ]);

    expect(screen.getByTestId('agent-card-POLICY_AGENT')).toBeInTheDocument();
    expect(container.textContent).not.toContain('→');
  });

  it('JSON-stringifies object output from an unknown tool instead of crashing', () => {
    renderBoard([{ tool: 'fraudScan', input: '"claim"', output: { score: 10 } }]);

    expect(screen.getByTestId('agent-card-POLICY_AGENT')).toBeInTheDocument();
    expect(screen.getByText(/\{"score":10\}/)).toBeInTheDocument();
  });

  it('passes string outputs through unchanged', () => {
    renderBoard([{ tool: 'legacyTool', input: '"x"', output: 'plain text result' }]);

    expect(screen.getByText(/plain text result/)).toBeInTheDocument();
  });
});

describe('PipelineBoard stage coverage', () => {
  it('renders one card for each of the six pipeline stages (backend PIPELINE_STAGES)', () => {
    renderBoard([]);

    // Regression: the shared constants dropped FRAUD_AGENT, so the live
    // board (and everything rendered from AGENT_ORDER) showed 5 of the
    // backend's 6 stages.
    for (const agent of [
      'INTAKE_AGENT',
      'POLICY_AGENT',
      'DOCUMENT_AGENT',
      'FRAUD_AGENT',
      'ELIGIBILITY_AGENT',
      'DECISION_AGENT',
    ]) {
      expect(screen.getByTestId(`agent-card-${agent}`)).toBeInTheDocument();
    }
  });
});

describe('formatToolOutput pure formatters', () => {
  it('summarizes found/missing/empty policy data', () => {
    expect(formatPolicyLookupOutput(POLICY_LOOKUP_FOUND.output)).toBe(
      'Policy AUTO-2024-001847 · Sarah Chen · coverage $50,000.00',
    );
    expect(formatPolicyLookupOutput({ found: false, data: null })).toBe(
      'Not found — no policy record matched this lookup',
    );
    expect(formatPolicyLookupOutput({ found: true, data: null })).toBe('Policy record found');
  });

  it('summarizes claim history counts singular, plural, and zero', () => {
    expect(formatClaimHistoryOutput({ count: 1, claims: [] })).toBe('1 prior claim in the last 12 months');
    expect(formatClaimHistoryOutput({ count: 3, claims: [] })).toBe('3 prior claims in the last 12 months');
    expect(formatClaimHistoryOutput({ count: 0, claims: [] })).toBe('No prior claims in the last 12 months');
  });

  it('returns null for absent output and strings for scalar output', () => {
    expect(formatToolOutput({ tool: 'policyLookup', output: null })).toBeNull();
    expect(formatToolOutput({ tool: 'legacyTool', output: 42 })).toBe('42');
    expect(formatToolOutput({ tool: 'legacyTool', output: { a: 1 } })).toBe('{"a":1}');
  });
});
