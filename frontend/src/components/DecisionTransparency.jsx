// F6: "Why this decision" — renders the deny-by-default projection's stage
// summaries and, on decided claims, the decision's plain-language summary and
// customer citations. Everything shown comes from the server projection
// (stageSummaries + decision); no internal trace data can appear here because
// it never crosses the projection boundary server-side. All copy comes from
// frontend/src/portalCopy.js — nothing hardcoded inline.
import { PORTAL_STAGE_COPY, WHY_THIS_DECISION_COPY } from '../portalCopy'

const STATUS_BADGES = {
  failed: 'border-[#ef4444]/40 bg-[#ef4444]/10 text-[#ef4444]',
  completed: 'border-[#10b981]/40 bg-[#10b981]/10 text-[#10b981]',
  skipped: 'border-[#3b82f6]/40 bg-[#3b82f6]/10 text-[#60a5fa]',
}

const statusBadge = (stageStatus) =>
  STATUS_BADGES[stageStatus] || STATUS_BADGES.skipped

export function DecisionTransparency({ stageSummaries, decision }) {
  if (!Array.isArray(stageSummaries) || stageSummaries.length === 0) {
    return null
  }

  return (
    <section
      data-testid="decision-transparency"
      aria-labelledby="why-this-decision-heading"
      className="mt-6 border border-[#1a1f2e] bg-[#0f1218] rounded-sm p-6"
    >
      <h2
        id="why-this-decision-heading"
        className="text-xs uppercase tracking-wider text-[#8892a4] font-mono"
      >
        {WHY_THIS_DECISION_COPY.heading}
      </h2>
      <p className="mt-2 text-sm text-[#8892a4]">{WHY_THIS_DECISION_COPY.intro}</p>

      <ol className="mt-5 space-y-5">
        {stageSummaries.map((stage) => {
          const fallback = PORTAL_STAGE_COPY[stage.stage] || {}
          return (
            <li key={stage.stage} className="border-l-2 border-[#1a1f2e] pl-4">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold text-[#e2e8f0]">
                  {stage.title || fallback.title || stage.stage}
                </h3>
                <span
                  data-testid={`transparency-status-${stage.stage}`}
                  className={`inline-flex items-center border px-2 py-0.5 rounded-full text-xs font-medium ${statusBadge(stage.status)}`}
                >
                  {stage.status === 'failed'
                    ? 'needs attention'
                    : stage.status === 'completed'
                      ? 'completed'
                      : 'skipped'}
                </span>
              </div>
              <p className="mt-1 text-sm text-[#8892a4]">
                {stage.summary || fallback.summary || ''}
              </p>
            </li>
          )
        })}
      </ol>

      {decision ? (
        <div
          data-testid="decision-summary"
          className="mt-6 border border-[#10b981]/30 bg-[#10b981]/5 px-4 py-3"
        >
          <h3 className="text-sm font-semibold text-[#e2e8f0]">
            {WHY_THIS_DECISION_COPY.decidedTitle}
          </h3>
          <p className="mt-1 text-sm text-[#e2e8f0]">{decision.summary}</p>
          {Array.isArray(decision.citations) && decision.citations.length > 0 && (
            <ul className="mt-3 space-y-2">
              {decision.citations.map((citation, index) => (
                <li
                  key={index}
                  data-testid="decision-citation"
                  className="text-sm text-[#8892a4]"
                >
                  {citation.customerFriendlyExplanation || citation.fact}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </section>
  )
}

export default DecisionTransparency
