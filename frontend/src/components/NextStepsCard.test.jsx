// NextStepsCard component test (AC-2.3) — the "What happens next" card renders
// the projection's next-step copy and ETA chip, and implements the quality
// bar's async states: skeleton rows while loading, a named error with Retry,
// an instructive empty state, and the content list with an optional ETA chip.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import NextStepsCard from './NextStepsCard';

const STEPS = [
  'We review your claim details and make sure we have everything we need.',
  "We check your policy to confirm what's covered.",
  "We prepare your decision and let you know as soon as it's ready.",
];

describe('NextStepsCard', () => {
  it('renders the numbered steps and the ETA chip', () => {
    render(
      <NextStepsCard
        steps={STEPS}
        eta="We aim to reach a decision within about 3 business days of receiving your claim."
      />
    );

    expect(screen.getByTestId('next-steps-card')).toBeInTheDocument();
    expect(screen.getByText('What happens next')).toBeInTheDocument();
    STEPS.forEach((step) => {
      expect(screen.getByText(step)).toBeInTheDocument();
    });
    expect(screen.getByTestId('status-expected-resolution')).toHaveTextContent(
      /within about 3 business days/
    );
  });

  it('omits the ETA chip when no ETA comes with the payload', () => {
    render(<NextStepsCard steps={STEPS} />);

    expect(screen.getByTestId('next-steps-card')).toBeInTheDocument();
    expect(screen.queryByTestId('status-expected-resolution')).not.toBeInTheDocument();
  });

  it('shows skeleton rows while loading — never an empty card or bare spinner', () => {
    render(<NextStepsCard steps={STEPS} loading />);

    expect(screen.getByTestId('next-steps-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('next-steps-card')).not.toBeInTheDocument();
    expect(screen.queryByTestId('status-expected-resolution')).not.toBeInTheDocument();
    // Skeleton mirrors the final layout: list rows, not a spinner glyph.
    expect(screen.getByRole('list').childElementCount).toBe(3);
  });

  it('renders a named error with Retry, and Retry re-runs the lookup', () => {
    const onRetry = vi.fn();
    render(<NextStepsCard steps={STEPS} error="The status service sent an unexpected response." onRetry={onRetry} />);

    expect(screen.getByTestId('next-steps-error')).toBeInTheDocument();
    expect(screen.getByText(/Couldn't load the next steps/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('next-steps-retry'));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('renders an instructive empty state when there are no steps', () => {
    render(<NextStepsCard steps={[]} />);

    expect(screen.getByTestId('next-steps-empty')).toBeInTheDocument();
    expect(screen.getByText(/no next steps to show/)).toBeInTheDocument();
    expect(screen.queryByTestId('next-steps-list')).not.toBeInTheDocument();
  });
});
