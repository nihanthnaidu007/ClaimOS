// Adjuster workbench queue: reviewable claims with status/severity/age
// filters, SLA aging badges, sorting, and live updates over the authenticated
// SSE queue stream. Row click opens the case view.
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertOctagon,
  ArrowDownUp,
  CheckCircle2,
  OctagonAlert,
  RefreshCw,
  Timer,
  Wifi,
  WifiOff,
} from 'lucide-react';
import api from '@/lib/api';
import { openWorkbenchStream } from '@/lib/workbenchStream';
import {
  formatCurrency,
  formatHours,
  severityPresentation,
  slaBadgeText,
  slaPresentation,
  statusClassName,
} from '@/lib/workbench';

const SLA_ICONS = { 'octagon-alert': OctagonAlert, timer: Timer, 'check-circle': CheckCircle2 };

const SORT_OPTIONS = [
  { value: 'age', label: 'Age (oldest first)' },
  { value: 'severity', label: 'Severity (elevated first)' },
  { value: 'risk', label: 'Risk (highest first)' },
];

const INITIAL_FILTERS = { status: '', severity: '', minAgeHours: '', maxAgeHours: '', sort: 'age' };

function LiveIndicator({ status }) {
  const presentation = {
    live: { label: 'Live', dot: 'bg-[#10b981]', Icon: Wifi },
    connecting: { label: 'Connecting…', dot: 'bg-[#8b96ab]', Icon: Wifi },
    reconnecting: { label: 'Reconnecting…', dot: 'bg-[#f59e0b]', Icon: Wifi },
    error: { label: 'Offline — showing last update', dot: 'bg-[#ef4444]', Icon: WifiOff },
  }[status] ?? { label: 'Connecting…', dot: 'bg-[#8b96ab]', Icon: Wifi };
  return (
    <span
      data-testid="live-indicator"
      data-status={status}
      className="inline-flex items-center gap-1.5 text-xs font-mono text-[#8b96ab]"
      role="status"
    >
      <span className={`inline-block w-2 h-2 rounded-full ${presentation.dot}`} />
      <presentation.Icon className="w-3 h-3" aria-hidden />
      {presentation.label}
    </span>
  );
}

function SlaBadge({ sla }) {
  const presentation = slaPresentation(sla);
  const Icon = SLA_ICONS[presentation.icon];
  return (
    <span
      data-testid="sla-badge"
      data-state={sla?.state}
      aria-label={slaBadgeText(sla)}
      className={`inline-flex items-center gap-1 border rounded px-1.5 py-0.5 text-[11px] font-mono ${presentation.className}`}
    >
      <Icon className="w-3 h-3" aria-hidden />
      {slaBadgeText(sla)}
    </span>
  );
}

export default function WorkbenchQueue() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [rows, setRows] = useState(null); // null = first load in flight
  const [error, setError] = useState(null);
  const [streamStatus, setStreamStatus] = useState('connecting');
  const [reloadKey, setReloadKey] = useState(0);
  const streamDisposeRef = useRef(null);

  const queryParams = useMemo(
    () => ({
      status: filters.status || undefined,
      severity: filters.severity || undefined,
      min_age_hours: filters.minAgeHours || undefined,
      max_age_hours: filters.maxAgeHours || undefined,
      sort: filters.sort,
    }),
    [filters]
  );

  // Initial + filter-change + manual-retry load.
  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .get('/workbench/queue', { params: queryParams })
      .then((res) => {
        if (!cancelled) setRows(res.data.rows);
      })
      .catch(() => {
        if (!cancelled) {
          setError('Could not load the review queue. Check your connection and try again.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [queryParams, reloadKey]);

  // Live updates: one stream per filter set; each frame replaces the rows.
  useEffect(() => {
    const dispose = openWorkbenchStream({
      path: '/workbench/stream',
      params: queryParams,
      onMessage: (message) => {
        if (message?.event === 'queue_update') {
          setRows(message.rows);
          setError(null);
        }
      },
      onStatus: setStreamStatus,
    });
    streamDisposeRef.current = dispose;
    return dispose;
  }, [queryParams]);

  function setFilter(key, value) {
    setFilters((prev) => ({ ...prev, [key]: value }));
  }

  const filterBar = (
    <div className="flex flex-wrap items-center gap-3" data-testid="queue-filters">
      <label className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
        Status
        <select
          value={filters.status}
          onChange={(e) => setFilter('status', e.target.value)}
          data-testid="filter-status"
          className="ml-2 bg-[#0d1119] border border-[#1a1f2e] rounded-md px-2 py-1.5 text-sm text-[#e2e8f0] normal-case focus:outline-none focus:border-[#3b82f6]"
        >
          <option value="">All reviewable</option>
          <option value="escalated">Escalated</option>
          <option value="pending">Pending</option>
          <option value="under_review">Under review</option>
        </select>
      </label>
      <label className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
        Severity
        <select
          value={filters.severity}
          onChange={(e) => setFilter('severity', e.target.value)}
          data-testid="filter-severity"
          className="ml-2 bg-[#0d1119] border border-[#1a1f2e] rounded-md px-2 py-1.5 text-sm text-[#e2e8f0] normal-case focus:outline-none focus:border-[#3b82f6]"
        >
          <option value="">All</option>
          <option value="elevated">Elevated</option>
          <option value="low">Low</option>
        </select>
      </label>
      <label className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
        Min age (h)
        <input
          type="number"
          min="0"
          value={filters.minAgeHours}
          onChange={(e) => setFilter('minAgeHours', e.target.value)}
          data-testid="filter-min-age"
          className="ml-2 w-20 bg-[#0d1119] border border-[#1a1f2e] rounded-md px-2 py-1.5 text-sm text-[#e2e8f0] normal-case focus:outline-none focus:border-[#3b82f6]"
        />
      </label>
      <label className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
        Max age (h)
        <input
          type="number"
          min="0"
          value={filters.maxAgeHours}
          onChange={(e) => setFilter('maxAgeHours', e.target.value)}
          data-testid="filter-max-age"
          className="ml-2 w-20 bg-[#0d1119] border border-[#1a1f2e] rounded-md px-2 py-1.5 text-sm text-[#e2e8f0] normal-case focus:outline-none focus:border-[#3b82f6]"
        />
      </label>
      <label className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
        <ArrowDownUp className="inline w-3 h-3 mr-1" aria-hidden />
        Sort
        <select
          value={filters.sort}
          onChange={(e) => setFilter('sort', e.target.value)}
          data-testid="filter-sort"
          className="ml-2 bg-[#0d1119] border border-[#1a1f2e] rounded-md px-2 py-1.5 text-sm text-[#e2e8f0] normal-case focus:outline-none focus:border-[#3b82f6]"
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );

  return (
    <div className="p-6 max-w-6xl mx-auto" data-testid="workbench-queue">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-[#e2e8f0]" style={{ fontFamily: 'Space Grotesk' }}>
            Review queue
          </h1>
          <p className="mt-1 text-sm text-[#8b96ab]">
            Claims awaiting an adjuster decision, oldest first. SLA targets come from the
            severity&apos;s configured hours.
          </p>
        </div>
        <LiveIndicator status={streamStatus} />
      </div>

      <div className="mt-4">{filterBar}</div>

      {error && (
        <div
          className="mt-4 flex items-center justify-between bg-[#ef4444]/10 border border-[#ef4444]/40 text-[#ef4444] rounded-lg px-4 py-3 text-sm"
          data-testid="queue-error"
          role="alert"
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setReloadKey((key) => key + 1)}
            data-testid="queue-retry"
            className="inline-flex items-center gap-1.5 underline hover:no-underline"
          >
            <RefreshCw className="w-3.5 h-3.5" aria-hidden />
            Retry
          </button>
        </div>
      )}

      {rows === null && !error && (
        <div className="mt-6 space-y-2" data-testid="queue-loading" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-12 bg-[#0d1119] border border-[#1a1f2e] rounded-lg animate-pulse" />
          ))}
        </div>
      )}

      {rows !== null && rows.length === 0 && !error && (
        <div className="mt-10 text-center text-sm text-[#8b96ab]" data-testid="queue-empty">
          <AlertOctagon className="w-8 h-8 mx-auto mb-3 text-[#4a5568]" aria-hidden />
          No claims awaiting review.
          <p className="mt-1 text-xs">New escalations appear here automatically as the pipeline runs.</p>
        </div>
      )}

      {rows !== null && rows.length > 0 && (
        <div className="mt-4 border border-[#1a1f2e] rounded-lg overflow-hidden" data-testid="queue-table">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-[#0d1119] text-[#8b96ab] text-xs uppercase tracking-wider font-mono">
                <th className="text-left px-4 py-2.5 font-medium">Claim</th>
                <th className="text-left px-4 py-2.5 font-medium">Holder</th>
                <th className="text-left px-4 py-2.5 font-medium">Type</th>
                <th className="text-right px-4 py-2.5 font-medium">Amount</th>
                <th className="text-left px-4 py-2.5 font-medium">Status</th>
                <th className="text-left px-4 py-2.5 font-medium">Severity</th>
                <th className="text-left px-4 py-2.5 font-medium">SLA</th>
                <th className="text-right px-4 py-2.5 font-medium">Age</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => navigate(`/workbench/claims/${encodeURIComponent(row.id)}`)}
                  data-testid={`queue-row-${row.id}`}
                  className="border-t border-[#1a1f2e] hover:bg-[#0d1119] cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3 font-mono text-[#7cb0ff]">{row.id}</td>
                  <td className="px-4 py-3 text-[#e2e8f0]">{row.holder_name || '—'}</td>
                  <td className="px-4 py-3 text-[#8b96ab]">{row.incident_type || '—'}</td>
                  <td className="px-4 py-3 text-right font-mono text-[#e2e8f0]">
                    {formatCurrency(row.claimed_amount)}
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className={`inline-block border rounded px-1.5 py-0.5 text-[11px] font-mono uppercase ${statusClassName(row.status)}`}
                      >
                        {(row.status || '—').replace('_', ' ')}
                      </span>
                      {(row.fraud_flags || []).length > 0 && (
                        <span
                          data-testid={`queue-fraud-flag-${row.id}`}
                          title={(row.fraud_flags || []).map(f => f.detail || f.code).join('; ')}
                          className={`inline-block border rounded px-1.5 py-0.5 text-[11px] font-mono font-bold uppercase ${(row.fraud_flags || []).some(f => f.severity === 'high') ? 'border-[#ef4444]/40 bg-[#ef4444]/10 text-[#ef4444]' : 'border-[#f59e0b]/40 bg-[#f59e0b]/10 text-[#f59e0b]'}`}
                        >
                          Flagged
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block border rounded px-1.5 py-0.5 text-[11px] font-mono ${severityPresentation(row.severity).className}`}
                    >
                      {severityPresentation(row.severity).label}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <SlaBadge sla={row.sla} />
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-[#8b96ab]">
                    {formatHours(row.sla?.hoursElapsed)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
