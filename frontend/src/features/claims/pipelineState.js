// Pure pipeline-state reducer: durable events → board state in the query cache.
//
// The event log is the transport; this module is the mapping the research doc
// calls the "eventMap": precise per-agent transitions patch the cache with
// zero round-trips, and terminal events flag which queries to invalidate so a
// refetch restores server truth. Kept pure so Vitest can pin every transition.
import { AGENT_ORDER } from './constants';

export const INITIAL_PIPELINE_STATE = {
  agents: {},
  status: 'running', // running | halted | failed | finalized
  haltReason: '',
  failureReason: '',
  finalizedStatus: null, // run status at run_finalized: auto_approved | escalated | failed | halted
  stp: null,
};

function setAgent(state, agent, patch) {
  return {
    ...state,
    agents: {
      ...state.agents,
      [agent]: { ...(state.agents[agent] || { status: 'waiting' }), ...patch },
    },
  };
}

// One SSE event → next state. Unknown event types are ignored (forward
// compatibility: an older board must not crash on a newer worker).
export function reducePipelineEvent(state, event) {
  switch (event.event) {
    case 'agent_start':
      return setAgent(state, event.agent, {
        status: 'running',
        output: null,
        toolsCalled: [],
        duration: 0,
        label: event.label,
      });

    case 'agent_complete':
      return setAgent(state, event.agent, {
        status: 'done',
        output: event.output || {},
        toolsCalled: event.toolsCalled || [],
        duration: event.duration || 0,
      });

    case 'agent_error':
      return setAgent(state, event.agent, {
        status: 'error',
        output: { error: event.error || 'Agent failed' },
        toolsCalled: [],
        duration: event.duration || 0,
      });

    case 'pipeline_halted': {
      // Remaining waiting agents freeze as halted; completed ones stay green —
      // an error never erases what already succeeded (UX quality bar).
      const agents = { ...state.agents };
      for (const name of AGENT_ORDER) {
        if (!agents[name] || agents[name].status === 'waiting') {
          agents[name] = { ...(agents[name] || {}), status: 'halted' };
        }
      }
      return { ...state, agents, status: 'halted', haltReason: event.reason || 'Unknown reason' };
    }

    case 'claim_failed':
      return { ...state, status: 'failed', failureReason: event.reason || 'Pipeline failure' };

    case 'stp_finalized':
      return {
        ...state,
        stp: { decision: 'auto_approved', confidence: event.confidence, severity: event.severity },
      };

    case 'stp_escalated':
      return {
        ...state,
        stp: { decision: 'escalated', reason: event.reason, confidence: event.confidence, severity: event.severity },
      };

    case 'run_finalized':
      return { ...state, status: 'finalized', finalizedStatus: event.status };

    case 'pipeline_error':
      return { ...state, status: 'failed', failureReason: event.error || 'Pipeline error' };

    default:
      return state;
  }
}

// Which cache prefixes a terminal event must refresh from the server.
export function queriesToInvalidate(state) {
  if (state.status === 'finalized' || state.status === 'failed' || state.status === 'halted') {
    return ['claims'];
  }
  return [];
}
