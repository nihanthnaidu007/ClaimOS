// RiskGauge is pure prop-driven logic — these tests pin the score-clamping,
// threshold, label, and color-band contract without needing the network.
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import RiskGauge from './RiskGauge';

// The progress arc is the only circle with a round line cap — queryable handle
// for both the band color and the dasharray math.
const getProgressArc = (score) => {
  const { container } = render(<RiskGauge score={score} />);
  return container.querySelector('circle[stroke-linecap="round"]');
};

const getLabel = () =>
  screen.getByTestId('risk-gauge').querySelector('span').textContent;

describe('RiskGauge score clamping', () => {
  it('renders the displayed score normalized into [0, 100]', () => {
    render(<RiskGauge score={150} />);
    const gauge = screen.getByTestId('risk-gauge');
    expect(gauge.querySelector('text')).toHaveTextContent('100');
  });

  it('clamps negative scores up to 0', () => {
    render(<RiskGauge score={-5} />);
    expect(screen.getByTestId('risk-gauge').querySelector('text')).toHaveTextContent('0');
  });

  it('uses the clamped value for the progress arc length', () => {
    // size 120 → radius 54 → circumference 2π·54 ≈ 339.292
    const circumference = 2 * Math.PI * 54;
    const arc = getProgressArc(150);
    const [progress] = arc.getAttribute('stroke-dasharray').split(' ');
    expect(parseFloat(progress)).toBeCloseTo(circumference, 5);
  });
});

describe('RiskGauge label mapping', () => {
  it.each([
    [0, 'LOW'],
    [29, 'LOW'],
    [30, 'MEDIUM'],
    [69, 'MEDIUM'],
    [70, 'HIGH'],
    [100, 'HIGH'],
  ])('score %i maps to label %s', (score, expected) => {
    render(<RiskGauge score={score} />);
    expect(getLabel()).toBe(expected);
  });
});

describe('RiskGauge color bands', () => {
  // jsdom serializes inline styles as rgb(), so band colors are asserted in
  // their rgb() form.
  const rgb = { '#10b981': 'rgb(16, 185, 129)', '#f59e0b': 'rgb(245, 158, 11)', '#ef4444': 'rgb(239, 68, 68)' };

  it.each([
    [10, '#10b981'],
    [50, '#f59e0b'],
    [85, '#ef4444'],
  ])('score %i uses band color %s on the progress arc', (score, color) => {
    const arc = getProgressArc(score);
    expect(arc.getAttribute('stroke')).toBe(color);
  });

  it.each([
    [10, '#10b981'],
    [50, '#f59e0b'],
    [85, '#ef4444'],
  ])('score %i colors the label text %s', (score, color) => {
    render(<RiskGauge score={score} />);
    const span = screen.getByTestId('risk-gauge').querySelector('span');
    expect(span.style.color).toBe(rgb[color]);
  });
});
