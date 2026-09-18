// Pipeline-state reducer tests — the SSE board's pure mapping. Pins the
// tool-input normalization (typed agents emit object arguments; the board
// renders inputs inline, so a raw object crashes React — the 2026-09-18
// pageError "object with keys {policy_number}") and the failed-run settle.
import { describe, it, expect } from 'vitest';
import { INITIAL_PIPELINE_STATE, reducePipelineEvent, normalizeToolCalls } from './pipelineState';

const running = (patch = {}) => ({ ...INITIAL_PIPELINE_STATE, ...patch });

describe('normalizeToolCalls', () => {
  it('JSON-stringifies object tool inputs (#31 typed agents)', () => {
    const tools = normalizeToolCalls([
      { tool: 'policy_lookup', input: { policy_number: 'AUTO-2024-001847' }, duration_ms: 91 },
    ]);
    expect(tools[0].input).toBe('{"policy_number":"AUTO-2024-001847"}');
    expect(tools[0].tool).toBe('policy_lookup');
    expect(tools[0].duration_ms).toBe(91);
  });

  it('passes string inputs through and tolerates null/absent inputs', () => {
    expect(normalizeToolCalls([{ tool: 't', input: 'raw text' }])[0].input).toBe('raw text');
    expect(normalizeToolCalls([{ tool: 't' }])[0].input).toBe('');
    expect(normalizeToolCalls(null)).toEqual([]);
  });
});

describe('reducePipelineEvent', () => {
  it('agent_complete normalizes object tool inputs before they reach the board', () => {
    const next = reducePipelineEvent(running(), {
      event: 'agent_complete',
      agent: 'POLICY_AGENT',
      toolsCalled: [{ tool: 'policy_lookup', input: { policy_number: 'X' } }],
    });
    expect(next.agents.POLICY_AGENT.status).toBe('done');
    expect(typeof next.agents.POLICY_AGENT.toolsCalled[0].input).toBe('string');
  });

  it('claim_failed settles the board (the stream terminates on it)', () => {
    const next = reducePipelineEvent(running(), {
      event: 'claim_failed',
      reason: 'Agent ELIGIBILITY_AGENT failed',
    });
    expect(next.status).toBe('failed');
    expect(next.failureReason).toBe('Agent ELIGIBILITY_AGENT failed');
  });

  it('run_finalized settles the board with the run status', () => {
    const next = reducePipelineEvent(running(), {
      event: 'run_finalized',
      status: 'escalated',
    });
    expect(next.status).toBe('finalized');
    expect(next.finalizedStatus).toBe('escalated');
  });
});
