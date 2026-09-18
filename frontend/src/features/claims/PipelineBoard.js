// Live pipeline board: five agent cards fed by the durable event stream.
import { useState } from 'react';
import { CheckCircle2, XCircle, AlertTriangle, Clock, Wrench, ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { AGENT_META, AGENT_ORDER, formatDollars } from './constants';

// Pure display formatters for tool-call I/O. The backend records tool output
// as structured objects (policyLookup → {found, data}, claimHistory →
// {count, claims}); React cannot render objects as children, so every value
// crossing into JSX goes through here and returns a string (or null to render
// nothing). The {found, data} shape is the backend contract — format it, never
// flatten it upstream.
export function formatPolicyLookupOutput(output) {
  if (output == null || typeof output !== 'object') return output == null ? '' : String(output);
  if (output.found !== true) return 'Not found — no policy record matched this lookup';
  const data = output.data || {};
  const bits = [];
  if (data.policy_number) bits.push(`Policy ${data.policy_number}`);
  if (data.holder_name) bits.push(data.holder_name);
  if (data.coverage_limit != null) bits.push(`coverage ${formatDollars(data.coverage_limit)}`);
  return bits.length > 0 ? bits.join(' · ') : 'Policy record found';
}

export function formatClaimHistoryOutput(output) {
  if (output == null || typeof output !== 'object') return output == null ? '' : String(output);
  if (typeof output.count !== 'number') return JSON.stringify(output);
  return output.count === 0
    ? 'No prior claims in the last 12 months'
    : `${output.count} prior claim${output.count === 1 ? '' : 's'} in the last 12 months`;
}

// One tool call → the arrow-suffix string on the trace line. Known tools get a
// human summary; any other object falls back to JSON so a new backend tool can
// never crash the board with an object-shaped output.
export function formatToolOutput(tool) {
  const output = tool?.output;
  if (output == null) return null;
  if (typeof output !== 'object') return String(output);
  if (tool.tool === 'policyLookup') return formatPolicyLookupOutput(output);
  if (tool.tool === 'claimHistory') return formatClaimHistoryOutput(output);
  return JSON.stringify(output);
}

const CONNECTION_META = {
  connecting: { label: 'CONNECTING', color: 'text-[#f59e0b]', dot: 'bg-[#f59e0b]' },
  live: { label: 'LIVE', color: 'text-[#10b981]', dot: 'bg-[#10b981]' },
  reconnecting: { label: 'RECONNECTING', color: 'text-[#f59e0b]', dot: 'bg-[#f59e0b]' },
  offline: { label: 'OFFLINE', color: 'text-[#ef4444]', dot: 'bg-[#ef4444]' },
};

// Header chip telling the user how healthy the stream is (UX quality bar:
// the board always says whether it is live or replaying a gap).
function ConnectionChip({ state }) {
  const meta = CONNECTION_META[state] || CONNECTION_META.offline;
  return (
    <span
      data-testid="connection-chip"
      className={`inline-flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider border border-[#232b3d] rounded-none px-2 py-0.5 ${meta.color}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
    </span>
  );
}

function AgentCard({ agentName, status, output, toolsCalled, duration, isLast }) {
  const meta = AGENT_META[agentName] || { index: '?', label: agentName, desc: '' };
  const [showTrace, setShowTrace] = useState(false);
  const tools = toolsCalled || [];

  const statusIcon = () => {
    switch (status) {
      case 'running': return <Loader2 className="w-4 h-4 text-[#3b82f6] animate-spin" />;
      case 'done': return <CheckCircle2 className="w-4 h-4 text-[#10b981]" />;
      case 'error': return <XCircle className="w-4 h-4 text-[#ef4444]" />;
      case 'halted': return <AlertTriangle className="w-4 h-4 text-[#f59e0b]" />;
      default: return <Clock className="w-4 h-4 text-[#4a5568]" />;
    }
  };

  const borderClass = () => {
    switch (status) {
      case 'running': return 'agent-running border-[#3b82f6]';
      case 'done': return 'border-[#10b981]/50';
      case 'error': return 'border-[#ef4444]/50';
      case 'halted': return 'border-[#f59e0b]/50';
      default: return 'border-[#1a1f2e] opacity-40';
    }
  };

  const statusLabel = () => {
    switch (status) {
      case 'running': return 'RUNNING';
      case 'done': return `DONE · ${((duration || 0) / 1000).toFixed(1)}s`;
      case 'error': return 'ERROR';
      case 'halted': return 'HALTED';
      default: return 'WAITING';
    }
  };

  return (
    <div className="relative" data-testid={`agent-card-${agentName}`}>
      {!isLast && (
        <div className="absolute left-[19px] top-[48px] bottom-[-16px] w-[2px] bg-[#1a1f2e] z-0" />
      )}

      <div className={`relative bg-[#0f1218] border rounded-sm overflow-hidden transition-all duration-300 ${borderClass()}`}>
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3">
          <div className={`relative z-10 flex items-center justify-center w-8 h-8 rounded-full border text-xs font-mono font-bold ${
            status === 'done' ? 'bg-[#10b981]/10 border-[#10b981]/30 text-[#10b981]' :
            status === 'running' ? 'bg-[#3b82f6]/10 border-[#3b82f6]/30 text-[#3b82f6]' :
            status === 'error' ? 'bg-[#ef4444]/10 border-[#ef4444]/30 text-[#ef4444]' :
            'bg-[#0a0c12] border-[#232b3d] text-[#4a5568]'
          }`}>
            {meta.icon}
          </div>
          <div className="flex-1 min-w-0">
            <span className="text-sm font-semibold text-[#e2e8f0] uppercase tracking-wide" style={{ fontFamily: 'Space Grotesk' }}>
              {meta.label}
            </span>
            <div className="text-xs text-[#4a5568] truncate">{meta.desc}</div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {statusIcon()}
            <span className={`text-[10px] font-mono uppercase tracking-wider ${
              status === 'done' ? 'text-[#10b981]' :
              status === 'running' ? 'text-[#3b82f6]' :
              status === 'error' ? 'text-[#ef4444]' :
              'text-[#4a5568]'
            }`}>
              {statusLabel()}
            </span>
          </div>
        </div>

        {/* Progress bar */}
        <div className="progress-bar-container">
          {status === 'running' && <div className="progress-bar-indeterminate" />}
          {status === 'done' && <div className="progress-bar-complete" />}
        </div>

        {/* Running state */}
        {status === 'running' && (
          <div className="px-4 py-2 border-t border-[#1a1f2e]/50">
            <div className="flex items-center gap-2 text-xs text-[#3b82f6] font-mono">
              Analyzing
              <span className="thinking-cursor" />
            </div>
            {tools.map((tool, i) => (
              <div key={i} className="tool-call-line flex items-center gap-2 text-xs font-mono text-[#8892a4] mt-1">
                <Wrench className="w-3 h-3 text-[#7dd3fc]" />
                <span className="text-[#7dd3fc]">{tool.tool}</span>
                <span className="text-[#4a5568]">({tool.input})</span>
              </div>
            ))}
          </div>
        )}

        {/* Done state: summary + tool calls + reasoning trace */}
        {status === 'done' && output && (
          <div className="border-t border-[#1a1f2e]/50">
            <AgentSummary output={output} toolCount={tools.length} />
            {tools.length > 0 && (
              <div className="px-4 pb-2">
                {tools.map((tool, i) => (
                  <div key={i} className="tool-call-line flex items-center gap-2 text-xs font-mono text-[#8892a4] mt-0.5">
                    <Wrench className="w-3 h-3 text-[#7dd3fc]" />
                    <span className="text-[#7dd3fc]">{tool.tool}</span>
                    <span className="text-[#4a5568]">(&quot;{tool.input}&quot;)</span>
                    {tool.duration_ms != null && <span className="text-[#10b981]">✓ {tool.duration_ms}ms</span>}
                    {tool.output != null && <span className="text-[#4a5568]">→ {formatToolOutput(tool)}</span>}
                  </div>
                ))}
              </div>
            )}
            {output.reasoning && (
              <div>
                <button
                  data-testid={`toggle-trace-${agentName}`}
                  onClick={() => setShowTrace(!showTrace)}
                  className="w-full flex items-center gap-2 px-4 py-2 text-xs font-mono text-[#3b82f6] hover:text-[#60a5fa] hover:bg-[#141820] transition-colors duration-200"
                >
                  {showTrace ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  {showTrace ? 'HIDE' : 'VIEW'} REASONING TRACE
                </button>
                {showTrace && (
                  <div className="px-4 pb-3 border-t border-[#1a1f2e]/50">
                    <pre className="text-[11px] font-mono text-[#8892a4] whitespace-pre-wrap leading-relaxed mt-2 bg-[#0a0c12] p-3 rounded-sm border border-[#1a1f2e] max-h-48 overflow-y-auto">
                      {output.reasoning}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Error state */}
        {status === 'error' && (
          <div className="px-4 py-2 border-t border-[#ef4444]/20 bg-[#ef4444]/5">
            <div className="text-xs font-mono text-[#ef4444]">{output?.error || 'Pipeline continuing in degraded mode'}</div>
          </div>
        )}
      </div>
    </div>
  );
}

// One-line summary metrics that differ per agent's output shape.
function AgentSummary({ output, toolCount }) {
  const riskColor = (score) => (score >= 70 ? 'text-[#ef4444]' : score >= 30 ? 'text-[#f59e0b]' : 'text-[#10b981]');
  return (
    <div className="px-4 py-2 flex items-center gap-4 text-xs font-mono text-[#8892a4] flex-wrap">
      {output.valid !== undefined && (
        <span>STATUS: <span className={output.valid ? 'text-[#10b981]' : 'text-[#ef4444]'}>{output.valid ? 'VALID' : 'INVALID'}</span></span>
      )}
      {output.found !== undefined && (
        <span>FOUND: <span className={output.found ? 'text-[#10b981]' : 'text-[#ef4444]'}>{output.found ? 'YES' : 'NO'}</span></span>
      )}
      {output.consistencyScore !== undefined && <span>CONSISTENCY: <span className="text-[#7dd3fc]">{output.consistencyScore}/100</span></span>}
      {output.riskScore !== undefined && <span>RISK: <span className={riskColor(output.riskScore)}>{output.riskScore}/100</span></span>}
      {output.confidence !== undefined && output.verdict && (
        <span>CONFIDENCE: <span className="text-[#7dd3fc]">{Math.round(output.confidence * 100)}%</span></span>
      )}
      {output.verdict && (
        <span>VERDICT: <span className={output.verdict === 'approved' ? 'text-[#10b981]' : output.verdict === 'rejected' ? 'text-[#ef4444]' : 'text-[#f59e0b]'}>{output.verdict?.toUpperCase()}</span></span>
      )}
      {toolCount > 0 && <span>TOOLS: {toolCount}</span>}
    </div>
  );
}

export default function PipelineBoard({ claimId, pipeline, connectionState }) {
  const { agents, status, haltReason, failureReason } = pipeline;

  return (
    <div className="page-enter" data-testid="pipeline-board">
      {/* Top bar */}
      <div className="flex items-center gap-3 mb-6">
        <div className="recording-dot" />
        <span className="text-xs uppercase tracking-[0.15em] text-[#8892a4] font-mono">Agent Pipeline</span>
        <span className="text-xs font-mono text-[#7dd3fc]">{claimId}</span>
        <ConnectionChip state={connectionState} />
      </div>

      {/* Agent cards */}
      <div className="space-y-4">
        {AGENT_ORDER.map((name, idx) => (
          <AgentCard
            key={name}
            agentName={name}
            status={agents[name]?.status || 'waiting'}
            output={agents[name]?.output}
            toolsCalled={agents[name]?.toolsCalled || []}
            duration={agents[name]?.duration || 0}
            isLast={idx === AGENT_ORDER.length - 1}
          />
        ))}
      </div>

      {/* Halted banner */}
      {status === 'halted' && (
        <div className="mt-4 bg-[#f59e0b]/5 border-2 border-dashed border-[#f59e0b] rounded-sm p-5 text-center" data-testid="pipeline-halted">
          <AlertTriangle className="w-6 h-6 text-[#f59e0b] mx-auto mb-2" />
          <div className="text-sm font-semibold text-[#f59e0b] uppercase mb-1">Pipeline Halted</div>
          <div className="text-xs font-mono text-[#8892a4]">{haltReason}</div>
        </div>
      )}

      {/* Failed banner */}
      {status === 'failed' && (
        <div className="mt-4 bg-[#ef4444]/5 border-2 border-dashed border-[#ef4444] rounded-sm p-5 text-center" data-testid="pipeline-failed">
          <XCircle className="w-6 h-6 text-[#ef4444] mx-auto mb-2" />
          <div className="text-sm font-semibold text-[#ef4444] uppercase mb-1">Pipeline Failed</div>
          <div className="text-xs font-mono text-[#8892a4]">{failureReason}</div>
        </div>
      )}
    </div>
  );
}
