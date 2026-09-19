import { useState, useEffect } from 'react';
import {
  Timer, Zap, ShieldAlert, PieChart as PieIcon, AlarmClock, RefreshCw, WifiOff, Users, Siren,
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell,
  PieChart, Pie, Legend,
} from 'recharts';
import api from '@/lib/api';

// Design language: the dark glass-box palette shared by Dashboard and
// ClaimHistory (panel / border / muted text / accent blue).
const PANEL = 'bg-[#0f1218] border border-[#1a1f2e] rounded-sm';
const GRID_STROKE = '#1a1f2e';
const AXIS_TICK = { fill: '#4a5568', fontSize: 10, fontFamily: 'JetBrains Mono' };

const STATUS_COLORS = {
  auto_approved: '#10b981',
  approved: '#10b981',
  escalated: '#f59e0b',
  under_review: '#f59e0b',
  rejected: '#ef4444',
  failed: '#ef4444',
  pending: '#8892a4',
};

const statusColor = (status) => STATUS_COLORS[status] || '#60a5fa';

const statusLabel = (status) =>
  String(status).replace(/_/g, ' ').toUpperCase();

const formatHours = (seconds) => `${(Number(seconds || 0) / 3600).toFixed(1)}h`;

const formatRate = (rate) => `${Math.round(Number(rate || 0) * 100)}%`;

function GroupHeader({ icon: Icon, color, title, subtitle }) {
  return (
    <div className="flex items-center justify-between mb-4">
      <div className="flex items-center gap-2">
        <Icon className={`w-4 h-4 ${color}`} strokeWidth={1.5} />
        <span className="text-xs uppercase tracking-[0.15em] text-[#8892a4] font-mono">{title}</span>
      </div>
      {subtitle && <span className="text-[10px] text-[#4a5568] font-mono uppercase">{subtitle}</span>}
    </div>
  );
}

function GroupCard({ testId, children, className = '' }) {
  return (
    <div data-testid={testId} className={`${PANEL} p-5 ${className}`}>
      {children}
    </div>
  );
}

function EmptyNote({ children }) {
  return (
    <div className="text-[11px] text-[#4a5568] font-mono mt-2">{children}</div>
  );
}

function ChartTooltip({ active, payload, label, formatter }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="bg-[#0a0c12] border border-[#1a1f2e] px-3 py-2 font-mono text-[11px] text-[#e2e8f0]">
      <div className="text-[#8892a4]">{formatter ? formatter(label) : label}</div>
      {payload.map((entry) => (
        <div key={entry.dataKey} style={{ color: entry.color || entry.payload?.fill }}>
          {entry.name}: {entry.value}
        </div>
      ))}
    </div>
  );
}

function Donut({ data, centerLabel, centerValue }) {
  return (
    <div className="relative" style={{ width: 200, height: 190 }}>
      <PieChart width={200} height={190}>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          innerRadius={55}
          outerRadius={80}
          startAngle={90}
          endAngle={-270}
          stroke="none"
          isAnimationActive={false}
        >
          {data.map((entry) => (
            <Cell key={entry.name} fill={entry.color} />
          ))}
        </Pie>
        <Tooltip content={<ChartTooltip />} />
        <Legend
          verticalAlign="bottom"
          height={24}
          formatter={(value) => (
            <span className="text-[10px] font-mono text-[#8892a4]">{value}</span>
          )}
        />
      </PieChart>
      <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none" style={{ paddingBottom: 24 }}>
        <div className="text-xl font-bold text-[#e2e8f0]" style={{ fontFamily: 'JetBrains Mono' }}>{centerValue}</div>
        <div className="text-[9px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">{centerLabel}</div>
      </div>
    </div>
  );
}

export default function OpsAnalytics() {
  const [analytics, setAnalytics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchAnalytics = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get('/analytics/ops');
      setAnalytics(res.data);
    } catch (e) {
      setError(e.response ? `Analytics unavailable (HTTP ${e.response.status})` : 'Analytics unavailable');
      console.error('Failed to fetch ops analytics:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAnalytics();
  }, []);

  if (loading) {
    return (
      <div className="page-enter" data-testid="ops-loading">
        <div className="flex items-center gap-3 mb-8">
          <PieIcon className="w-5 h-5 text-[#3b82f6]" />
          <h1 className="text-2xl font-bold tracking-tight uppercase" style={{ fontFamily: 'Space Grotesk' }}>
            Ops Analytics
          </h1>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-5 animate-pulse h-64" />
          ))}
        </div>
      </div>
    );
  }

  if (error || !analytics) {
    return (
      <div className="page-enter" data-testid="ops-error">
        <div className="flex items-center gap-3 mb-8">
          <PieIcon className="w-5 h-5 text-[#3b82f6]" />
          <h1 className="text-2xl font-bold tracking-tight uppercase" style={{ fontFamily: 'Space Grotesk' }}>
            Ops Analytics
          </h1>
        </div>
        <div className={`${PANEL} p-8 flex flex-col items-center gap-4`}>
          <WifiOff className="w-8 h-8 text-[#ef4444]" strokeWidth={1.5} />
          <div className="text-sm text-[#8892a4] font-mono">{error || 'Analytics unavailable'}</div>
          <button
            data-testid="ops-retry"
            onClick={fetchAnalytics}
            className="flex items-center gap-2 border border-[#3b82f6]/50 text-[#3b82f6] hover:bg-[#3b82f6]/10 text-sm font-mono px-4 py-2 rounded-none transition-colors duration-200"
          >
            <RefreshCw className="w-4 h-4" /> Retry
          </button>
        </div>
      </div>
    );
  }

  const { cycleTime, stp, fraud, decisions, sla, workload, escalations } = analytics;
  const isBookEmpty = fraud.totalClaims === 0 && cycleTime.decided === 0;
  const maxOpen = Math.max(...(workload?.adjusters || []).map((row) => row.openClaims), 0);

  const stpData = [
    { name: 'Auto-approved', value: stp.autoApproved, color: '#10b981' },
    { name: 'Escalated', value: stp.escalated, color: '#f59e0b' },
  ].filter((slice) => slice.value > 0);

  const fraudData = [
    { name: 'Flagged', value: fraud.flaggedClaims, color: '#ef4444' },
    { name: 'Clean', value: Math.max(fraud.totalClaims - fraud.flaggedClaims, 0), color: '#23324a' },
  ];

  const slaData = sla.bySeverity.map((row) => ({
    severity: row.severity,
    'Within SLA': Math.max(row.decided - row.breaches, 0),
    'Breached': row.breaches,
  }));

  return (
    <div className="page-enter" data-testid="ops-analytics">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <PieIcon className="w-5 h-5 text-[#3b82f6]" />
          <h1 className="text-2xl font-bold tracking-tight uppercase" style={{ fontFamily: 'Space Grotesk' }}>
            Ops Analytics
          </h1>
        </div>
        <button
          data-testid="ops-refresh"
          onClick={fetchAnalytics}
          className="flex items-center gap-2 border border-[#1a1f2e] text-[#8892a4] hover:text-[#e2e8f0] hover:border-[#3b82f6]/50 text-xs font-mono px-3 py-2 rounded-none transition-colors duration-200 uppercase tracking-wide"
        >
          <RefreshCw className="w-3.5 h-3.5" /> Refresh
        </button>
      </div>

      {isBookEmpty && (
        <div data-testid="ops-empty" className={`${PANEL} px-5 py-3 mb-6 text-[11px] text-[#8892a4] font-mono`}>
          No claims processed yet — metrics populate as claims move through the pipeline.
        </div>
      )}

      {/* Group 1: Cycle time percentiles */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6 stagger-children">
        <GroupCard testId="ops-cycle-time">
          <GroupHeader icon={Timer} color="text-[#3b82f6]" title="Cycle Time" subtitle="submitted → decided" />
          <div className="grid grid-cols-3 gap-3 mb-4">
            <div>
              <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">p50</div>
              <div className="text-2xl font-bold text-[#e2e8f0]" style={{ fontFamily: 'JetBrains Mono' }} data-testid="ops-cycle-p50">
                {formatHours(cycleTime.p50Seconds)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">p95</div>
              <div className="text-2xl font-bold text-[#e2e8f0]" style={{ fontFamily: 'JetBrains Mono' }} data-testid="ops-cycle-p95">
                {formatHours(cycleTime.p95Seconds)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">Decided</div>
              <div className="text-2xl font-bold text-[#e2e8f0]" style={{ fontFamily: 'JetBrains Mono' }}>
                {cycleTime.decided}
              </div>
            </div>
          </div>
          <BarChart
            width={460}
            height={150}
            data={[
              { name: 'p50', seconds: cycleTime.p50Seconds },
              { name: 'p95', seconds: cycleTime.p95Seconds },
            ]}
            margin={{ top: 4, right: 12, bottom: 0, left: 12 }}
          >
            <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="name" tick={AXIS_TICK} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
            <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={44} />
            <Tooltip content={<ChartTooltip formatter={() => 'Cycle time (seconds)'} />} cursor={{ fill: '#141820' }} />
            <Bar dataKey="seconds" name="Seconds" isAnimationActive={false}>
              <Cell fill="#3b82f6" />
              <Cell fill="#f59e0b" />
            </Bar>
          </BarChart>
        </GroupCard>

        {/* Group 2: STP rate */}
        <GroupCard testId="ops-stp-rate">
          <GroupHeader icon={Zap} color="text-[#10b981]" title="STP Rate" subtitle="auto-approval" />
          <div className="flex items-center gap-6">
            <Donut
              data={stpData.length ? stpData : [{ name: 'Decided', value: 0, color: '#23324a' }]}
              centerLabel="auto-approvals"
              centerValue={formatRate(stp.rate)}
            />
            <div className="space-y-3">
              <div>
                <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">Auto-approved</div>
                <div className="text-xl font-bold text-[#10b981]" style={{ fontFamily: 'JetBrains Mono' }}>{stp.autoApproved}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">Escalated</div>
                <div className="text-xl font-bold text-[#f59e0b]" style={{ fontFamily: 'JetBrains Mono' }}>{stp.escalated}</div>
              </div>
              <EmptyNote>
                {stp.decided === 0 ? 'No decided claims yet' : `${stp.decided} decided through the STP gate`}
              </EmptyNote>
            </div>
          </div>
        </GroupCard>

        {/* Group 3: Fraud-flag rate */}
        <GroupCard testId="ops-fraud-rate">
          <GroupHeader icon={ShieldAlert} color="text-[#ef4444]" title="Fraud-Flag Rate" subtitle="cross-check signals" />
          <div className="flex items-center gap-6">
            <Donut data={fraudData} centerLabel="flagged" centerValue={formatRate(fraud.rate)} />
            <div className="space-y-3">
              <div>
                <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">Flagged claims</div>
                <div className="text-xl font-bold text-[#ef4444]" style={{ fontFamily: 'JetBrains Mono' }}>{fraud.flaggedClaims}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">Total claims</div>
                <div className="text-xl font-bold text-[#e2e8f0]" style={{ fontFamily: 'JetBrains Mono' }}>{fraud.totalClaims}</div>
              </div>
              <EmptyNote>
                {fraud.totalClaims === 0 ? 'No claims on the book yet' : 'Flags come from the fraud cross-check stage'}
              </EmptyNote>
            </div>
          </div>
        </GroupCard>

        {/* Group 4: Decision distribution */}
        <GroupCard testId="ops-decisions">
          <GroupHeader icon={PieIcon} color="text-[#3b82f6]" title="Decision Distribution" subtitle="whole book" />
          {decisions.length === 0 ? (
            <EmptyNote>No decisions recorded yet.</EmptyNote>
          ) : (
            <BarChart width={460} height={230} data={decisions} margin={{ top: 4, right: 12, bottom: 0, left: 12 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="status" tickFormatter={statusLabel} tick={{ ...AXIS_TICK, fontSize: 9 }} axisLine={{ stroke: GRID_STROKE }} tickLine={false} interval={0} angle={-18} textAnchor="end" height={44} />
              <YAxis allowDecimals={false} tick={AXIS_TICK} axisLine={false} tickLine={false} width={30} />
              <Tooltip content={<ChartTooltip formatter={statusLabel} />} cursor={{ fill: '#141820' }} />
              <Bar dataKey="count" name="Claims" isAnimationActive={false}>
                {decisions.map((entry) => (
                  <Cell key={entry.status} fill={statusColor(entry.status)} />
                ))}
              </Bar>
            </BarChart>
          )}
        </GroupCard>

        {/* Group 5: SLA breaches by severity */}
        <GroupCard testId="ops-sla" className="lg:col-span-2">
          <GroupHeader icon={AlarmClock} color="text-[#f59e0b]" title="SLA Breaches" subtitle="by derived severity" />
          <div className="flex items-start gap-8">
            <BarChart width={560} height={230} data={slaData} margin={{ top: 4, right: 12, bottom: 0, left: 12 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="severity" tick={AXIS_TICK} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <YAxis allowDecimals={false} tick={AXIS_TICK} axisLine={false} tickLine={false} width={30} />
              <Tooltip content={<ChartTooltip formatter={(v) => String(v).toUpperCase()} />} cursor={{ fill: '#141820' }} />
              <Legend
                formatter={(value) => (
                  <span className="text-[10px] font-mono text-[#8892a4]">{value}</span>
                )}
              />
              <Bar dataKey="Within SLA" stackId="sla" fill="#10b981" isAnimationActive={false} />
              <Bar dataKey="Breached" stackId="sla" fill="#ef4444" isAnimationActive={false} />
            </BarChart>
            <div className="space-y-3">
              {sla.bySeverity.map((row) => (
                <div key={row.severity} data-testid={`ops-sla-${row.severity}`}>
                  <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">
                    {row.severity} · SLA {row.slaHours}h
                  </div>
                  <div className="text-sm font-mono text-[#e2e8f0]">
                    {row.breaches} breach{row.breaches === 1 ? '' : 'es'} / {row.decided} decided
                    <span className="text-[#4a5568]"> ({formatRate(row.breachRate)})</span>
                  </div>
                </div>
              ))}
              <EmptyNote>Severity derives from amount and incident type (same rules as the STP gate).</EmptyNote>
            </div>
          </div>
        </GroupCard>

        {/* Group 6: Workload per adjuster (spec F10) */}
        <GroupCard testId="ops-workload">
          <GroupHeader icon={Users} color="text-[#3b82f6]" title="Workload" subtitle="open claims per adjuster" />
          {(workload?.adjusters || []).length === 0 ? (
            <EmptyNote>No active adjusters yet.</EmptyNote>
          ) : (
            <div className="space-y-3">
              {workload.adjusters.map((row) => (
                <div key={row.assigneeId} data-testid={`ops-workload-${row.assigneeId}`}>
                  <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">
                    <span>{row.email || row.assigneeId}</span>
                    <span className="text-[#e2e8f0]">
                      {row.openClaims} open claim{row.openClaims === 1 ? '' : 's'}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
                    <div
                      className="h-full bg-[#3b82f6] rounded-full"
                      style={{ width: `${maxOpen ? Math.round((row.openClaims / maxOpen) * 100) : 0}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
          <EmptyNote>
            <span data-testid="ops-workload-unassigned">
              {workload?.unassigned ?? 0} unassigned open claim{(workload?.unassigned ?? 0) === 1 ? '' : 's'}
            </span>
          </EmptyNote>
        </GroupCard>

        {/* Group 7: SLA escalations (spec F9) — the only surface where
            unassigned escalations appear; no bell can target them. */}
        <GroupCard testId="ops-escalations">
          <GroupHeader icon={Siren} color="text-[#ef4444]" title="Escalations" subtitle="SLA threshold crossed" />
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">On record</div>
              <div
                className="text-2xl font-bold text-[#e2e8f0] font-mono"
                style={{ fontFamily: 'JetBrains Mono' }}
                data-testid="ops-escalations-total"
              >
                {escalations?.total ?? 0}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">Unassigned</div>
              <div
                className="text-2xl font-bold text-[#ef4444] font-mono"
                style={{ fontFamily: 'JetBrains Mono' }}
                data-testid="ops-escalations-unassigned"
              >
                {escalations?.unassigned ?? 0}
              </div>
            </div>
          </div>
          <EmptyNote>
            Escalation is a record, not a toggle — it is set once when a claim crosses its SLA
            escalation threshold and is never cleared. Unassigned escalations surface only here.
          </EmptyNote>
        </GroupCard>
      </div>
    </div>
  );
}
