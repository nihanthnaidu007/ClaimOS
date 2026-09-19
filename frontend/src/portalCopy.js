// F6 decision transparency: the single editable home for customer-facing
// portal copy. Components must render copy from here — never hardcode stage
// wording inline — so product can tune how the pipeline is explained to
// customers without touching render logic. Mirrors backend
// app/portal_projection.py PORTAL_STAGE_COPY (kept in sync by tests on both
// sides: copy-constant drift is a test failure, not a silent mismatch).
export const PORTAL_STAGE_COPY = {
  intake: {
    title: 'Reviewing your claim',
    summary:
      'We received your claim and checked that all the required details were included.',
  },
  policy: {
    title: 'Verifying your coverage',
    summary:
      'We confirmed your policy was active and the incident is the kind it covers.',
  },
  documents: {
    title: 'Reviewing your documents',
    summary:
      'We reviewed the documents you provided and checked them for consistency.',
  },
  fraud: {
    title: 'Running standard checks',
    summary:
      'We completed the routine verification checks that are part of every claim.',
  },
  eligibility: {
    title: 'Checking eligibility',
    summary:
      'We reviewed how your claim fits the standard approval guidelines.',
  },
  decision: {
    title: 'Making the decision',
    summary:
      'We brought everything together and made the final decision on your claim.',
  },
}

// Section heading + supporting copy for the transparency section itself.
export const WHY_THIS_DECISION_COPY = {
  heading: 'Why this decision',
  intro:
    'Here is what happened at each step of reviewing your claim, in plain language.',
  citationsHeading: 'Why we made this call',
  decidedTitle: 'The decision, in plain language',
}

// F7 settlement card: heading + the plain-language payment-timing next step,
// rendered only once a settlement is recorded. The card shows what the
// settlement record actually carries (amount, recorded date) and never
// invents payment-method or fee data — the record doesn't track it (no
// payment rails), so the copy promises nothing the system can't back.
export const SETTLEMENT_CARD_COPY = {
  heading: 'Settlement recorded',
  paymentTiming:
    "Your settlement has been recorded. We don't have a payment date to share yet — payment timing depends on processing, and we'll keep you updated here.",
}

// Dev-only drift guard: if the backend adds or renames a stage key, this
// mismatch surfaces in CI (the RTL suite asserts every projection stage key
// has copy here) instead of rendering a blank section in production.
export const STAGE_KEYS = Object.keys(PORTAL_STAGE_COPY)
