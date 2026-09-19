// F6: RTL coverage for the "Why this decision" section — renders for decided
// claims, carries the projection's stage summaries, and shows nothing extra
// for in-flight claims (no decision block until a decision exists).
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import DecisionTransparency from './DecisionTransparency'
import { PORTAL_STAGE_COPY, STAGE_KEYS } from '../portalCopy'

const DECIDED_STAGES = [
  { stage: 'intake', title: 'Reviewing your claim', summary: 'We received your claim.', status: 'completed', citations: [] },
  { stage: 'policy', title: 'Verifying your coverage', summary: 'We confirmed your policy.', status: 'completed', citations: [] },
  { stage: 'documents', title: 'Reviewing your documents', summary: 'We reviewed your documents.', status: 'completed', citations: [] },
  { stage: 'fraud', title: 'Running standard checks', summary: 'We completed routine checks.', status: 'completed', citations: [] },
  { stage: 'eligibility', title: 'Checking eligibility', summary: 'We reviewed the guidelines.', status: 'completed', citations: [] },
  { stage: 'decision', title: 'Making the decision', summary: 'We made the final decision.', status: 'completed', citations: [] },
]

const DECIDED_DECISION = {
  summary: 'Approved: covered incident within limits.',
  citations: [
    {
      fact: 'Policy active on the incident date',
      sourceRef: 'policy.status=active',
      customerFriendlyExplanation: 'Your policy was active when the incident happened.',
    },
  ],
}

describe('DecisionTransparency', () => {
  it('renders nothing when there are no stage summaries', () => {
    const { container } = render(<DecisionTransparency stageSummaries={[]} decision={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the section with all six stage summaries on a decided claim', () => {
    render(
      <DecisionTransparency stageSummaries={DECIDED_STAGES} decision={DECIDED_DECISION} />,
    )

    expect(screen.getByRole('heading', { name: /why this decision/i })).toBeInTheDocument()
    for (const key of STAGE_KEYS) {
      expect(screen.getByText(PORTAL_STAGE_COPY[key].title)).toBeInTheDocument()
    }
    expect(screen.getByTestId('decision-summary')).toBeInTheDocument()
    expect(screen.getByText('Approved: covered incident within limits.')).toBeInTheDocument()
    // The customer citation renders its explanation, not internal source refs.
    expect(
      screen.getByText('Your policy was active when the incident happened.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('policy.status=active')).not.toBeInTheDocument()
  })

  it('has no decision block for in-flight claims', () => {
    render(
      <DecisionTransparency
        stageSummaries={DECIDED_STAGES.slice(0, 2)}
        decision={null}
      />,
    )

    expect(screen.queryByTestId('decision-summary')).not.toBeInTheDocument()
    expect(screen.getByText('Reviewing your claim')).toBeInTheDocument()
    expect(screen.getByText('Verifying your coverage')).toBeInTheDocument()
  })

  it('falls back to portal copy when a stage has no server copy', () => {
    render(
      <DecisionTransparency
        stageSummaries={[{ stage: 'intake', title: '', summary: '', status: 'completed', citations: [] }]}
        decision={null}
      />,
    )
    expect(screen.getByText(PORTAL_STAGE_COPY.intake.title)).toBeInTheDocument()
    expect(screen.getByText(PORTAL_STAGE_COPY.intake.summary)).toBeInTheDocument()
  })

  it('renders every stage with copy so new stages fail here, not in production', () => {
    // Drift guard: every stage the backend can project must have copy.
    for (const key of STAGE_KEYS) {
      expect(PORTAL_STAGE_COPY[key].title).toBeTruthy()
      expect(PORTAL_STAGE_COPY[key].summary).toBeTruthy()
    }
  })

  it('renders the failed status as a needs-attention badge', () => {
    render(
      <DecisionTransparency
        stageSummaries={[{ stage: 'documents', title: 'Reviewing your documents', summary: '...', status: 'failed', citations: [] }]}
        decision={null}
      />,
    )
    const section = screen.getByTestId('decision-transparency')
    expect(within(section).getByText('needs attention')).toBeInTheDocument()
  })
})
