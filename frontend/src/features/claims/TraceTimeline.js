// Trace timeline: the full per-agent adjudication record for one claim.
//
// Rendered from the durable trace endpoint (durable events + claim_runs +
// agent_logs), never from live stream state — an adjuster opening a claim
// days later sees exactly what the pipeline recorded, in order, including
// tool calls and findings.
import { useMemo, useState } from 'react';
import {
  CheckCircle2, XCircle, Clock, Wrench, ChevronDown, ChevronRight, AlertTriangle,
} from 'lucide-react';
import { AGENT_ORDER } from './constants';

const STATUS_META = {
  done: { icon: CheckCircle2, cls: 'text-[#10b981]', label: 'Completed' },
  running: { icon: Clock, cls: 'text-[#f59e0b]', label: 'Running' },
  error: { icon: XCircle, cls: 'text-[#ef4444]', label: 'Failed' },
  waiting: { icon: Clock, cls: 'text-[#4a5568]', label: 'Not reached' },
  halted: { icon: AlertTriangle, cls: 'text-[#f59e0b]', label: 'Halted' },
};

function statusMeta(status) {
  return STATUS_META[status] || STATUS_META.waiting;
}

// Compact summary of an agent's persisted output — findings-first, not a raw
// JSON dump. Unknown shapes fall back to a truncated JSON rendering.
export function summarizeOutput(output) {
  if (!output || typeof output !== 'object') return [];
  const facts = [];
  for (const [key, value] of Object.entries(output)) {
    if (value === null || value === '') continue;
    if (Array.isArray(value) && value.length === 0) continue;
    if (key === 'toolsCalled' || key === 'toolCalls') continue;
    if (typeof value === 'object') {
      facts.push({ key, value: JSON.stringify(value).slice(0, 200) });
    } else {
      facts.push({ key, value: String(value).slice(0, 200) });
    }
  }
  return facts;
}

function ToolCalls({ tools }) {
  const [open, setOpen] = useState(false);
  if (!tools || tools.length === 0) return null;
  return (
    <div className="tool-call-list mt-2" data-testid={`tool-calls-${tools.length}`}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-[11px] font-mono text-[#8892a4] hover:text-[#e2e8f0] transition-colors duration-200"
        data-testid="tool-calls-toggle"
      >
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        <Wrench className="w-3 h-3" /> {tools.length} tool call{tools.length === 1 ? '' : 's'}
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5 pl-4">
          {tools.map((tool, i) => (
            <li key={i} className="text-[11px] font-mono text-[#8892a4]">
              {typeof tool === 'string' ? tool : JSON.stringify(tool)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AgentCard({ log }) {
  const meta = statusMeta(log.status || 'done');
  const Icon = meta.icon;
  const facts = useMemo(() => summarizeOutput(log.output), [log.output]);
  const tools = log.toolsCalled || log.toolCalls || [];

  return (
    <div
      className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-4"
      data-testid={`trace-agent-${log.agent}`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon className={`w-4 h-4 ${meta.cls}`} />
          <span className="text-sm font-semibold text-[#e2e8f0]" style={{ fontFamily: 'Space Grotesk' }}>
            {log.label || log.agent}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[11px] font-mono">
          {log.confidence != null && log.confidence !== '' && (
            <span className="text-[#8892a4]" data-testid="trace-confidence">
              confidence {(Number(log.confidence) * 100).toFixed(0)}%
            </span>
          )}
          {log.duration ? <span className="text-[#4a5568]">{log.duration}ms</span> : null}
          <span className={meta.cls}>{meta.label}</span>
        </div>
      </div>

      {log.error && (
        <p className="text-xs font-mono text-[#ef4444] mt-2" data-testid="trace-agent-error">
          {log.error}
        </p>
      )}

      {facts.length > 0 && (
        <dl className="mt-3 space-y-1">
          {facts.map((fact) => (
            <div key={fact.key} className="grid grid-cols-[140px_1fr] gap-2 text-xs font-mono">
              <dt className="text-[#4a5568] uppercase tracking-wider truncate">{fact.key}</dt>
              <dd className="text-[#c9d2e3] break-words">{fact.value}</dd>
            </div>
          ))}
        </dl>
      )}

      <ToolCalls tools={tools} />
    </div>
  );
}

function EventRow({ event }) {
  const summary = useMemo(() => {
    const data = event.data || {};
    return Object.entries(data)
      .filter(([, v]) => v !== null && v !== '' && typeof v !== 'object')
      .slice(0, 3)
      .map(([k, v]) => `${k}=${v}`)
      .join(' · ');
  }, [event.data]);
  const time = event.createdAt ? event.createdAt.replace('T', ' ').slice(0, 19) : '';

  return (
    <li className="flex items-baseline gap-2 text-[11px] font-mono py-1 border-b border-[#131826] last:border-0">
      <span className="text-[#4a5568] shrink-0">#{event.seq}</span>
      <span className={`shrink-0 ${
        event.event.includes('error') || event.event.includes('failed') ? 'text-[#ef4444]' :
        event.event.includes('finalized') || event.event.includes('complete') ? 'text-[#10b981]' :
        'text-[#7dd3fc]'
      }`}>
        {event.event}
      </span>
      <span className="text-[#8892a4] truncate">{summary}</span>
      <span className="ml-auto text-[#2d3548] shrink-0">{time}</span>
    </li>
  );
}

export default function TraceTimeline({ trace }) {
  const logs = useMemo(() => trace?.agentLogs || [], [trace]);
  const ordered = useMemo(
    () =>
      [...logs].sort(
        (a, b) => AGENT_ORDER.indexOf(a.agent) - AGENT_ORDER.indexOf(b.agent),
      ),
    [logs],
  );
  const events = trace?.events || [];
  const runs = trace?.runs || [];

  if (!logs.length && !events.length) {
    return (
      <div className="text-xs font-mono text-[#4a5568] py-8 text-center" data-testid="trace-empty">
        No agent trace has been recorded for this claim yet.
      </div>
    );
  }

  return (
    <div className="trace-timeline space-y-6" data-testid="trace-timeline">
      {runs.length > 0 && (
        <div className="flex flex-wrap gap-2" data-testid="trace-runs">
          {runs.map((run) => (
            <span
              key={run.attempt}
              className="text-[11px] font-mono px-2 py-1 border border-[#232b3d] rounded-sm text-[#8892a4]"
            >
              run #{run.attempt}: {run.status}
              {run.failureReason ? ` — ${run.failureReason}` : ''}
              {run.escalationReason ? ` — escalated: ${run.escalationReason}` : ''}
            </span>
          ))}
        </div>
      )}

      <div className="space-y-3">
        {ordered.map((log) => (
          <AgentCard key={log.agent || log.label} log={log} />
        ))}
      </div>

      {events.length > 0 && (
        <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-4">
          <h3
            className="text-xs uppercase tracking-wider text-[#8892a4] font-mono mb-3"
            style={{ fontFamily: 'Space Grotesk' }}
          >
            Durable event log ({events.length})
          </h3>
          <ul data-testid="trace-events">
            {events.map((event) => (
              <EventRow key={event.seq} event={event} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
