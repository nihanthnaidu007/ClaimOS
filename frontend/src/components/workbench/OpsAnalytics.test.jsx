// OpsAnalytics component test — the MSW-intercepted happy path, the error
// state with retry, and the zeroed-book empty notice.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import OpsAnalytics from './OpsAnalytics';
import { server, opsAnalytics } from '../../test/handlers';

describe('OpsAnalytics', () => {
  it('renders the five metric groups from the mocked analytics API', async () => {
    render(<OpsAnalytics />);

    await waitFor(() =>
      expect(screen.getByTestId('ops-analytics')).toBeInTheDocument()
    );

    // The five metric groups (AC-9).
    expect(screen.getByTestId('ops-cycle-time')).toBeInTheDocument();
    expect(screen.getByTestId('ops-stp-rate')).toBeInTheDocument();
    expect(screen.getByTestId('ops-fraud-rate')).toBeInTheDocument();
    expect(screen.getByTestId('ops-decisions')).toBeInTheDocument();
    expect(screen.getByTestId('ops-sla')).toBeInTheDocument();

    // Exact fixture values: p50 54000s = 15.0h, p95 129600s = 36.0h.
    expect(screen.getByTestId('ops-cycle-p50')).toHaveTextContent('15.0h');
    expect(screen.getByTestId('ops-cycle-p95')).toHaveTextContent('36.0h');
    // STP rate 11/18 -> 61%.
    expect(screen.getByText('61%')).toBeInTheDocument();
    // SLA breach rate surfaces per severity (2/7 = 29%).
    expect(screen.getByTestId('ops-sla-elevated')).toHaveTextContent('2 breaches / 7 decided');
    expect(screen.getByTestId('ops-sla-low')).toHaveTextContent('0 breaches / 11 decided');
  });

  it('shows the empty-book notice when nothing has been processed', async () => {
    server.use(
      http.get(`${import.meta.env.VITE_API_BASE_URL}/api/analytics/ops`, () =>
        HttpResponse.json({
          cycleTime: { p50Seconds: 0, p95Seconds: 0, decided: 0 },
          stp: { decided: 0, autoApproved: 0, escalated: 0, rate: 0 },
          fraud: { totalClaims: 0, flaggedClaims: 0, rate: 0 },
          decisions: [],
          sla: {
            bySeverity: [
              { severity: 'elevated', slaHours: 24, decided: 0, breaches: 0, breachRate: 0 },
              { severity: 'low', slaHours: 48, decided: 0, breaches: 0, breachRate: 0 },
            ],
          },
        })
      )
    );

    render(<OpsAnalytics />);

    await waitFor(() =>
      expect(screen.getByTestId('ops-empty')).toBeInTheDocument()
    );
    expect(screen.getByTestId('ops-analytics')).toBeInTheDocument();
  });

  it('shows the error state with a working retry button when the API fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    server.use(
      http.get(
        `${import.meta.env.VITE_API_BASE_URL}/api/analytics/ops`,
        () => HttpResponse.json({ detail: 'database unavailable' }, { status: 500 })
      )
    );

    render(<OpsAnalytics />);

    await waitFor(() =>
      expect(screen.getByTestId('ops-error')).toBeInTheDocument()
    );
    expect(screen.getByText(/HTTP 500/)).toBeInTheDocument();

    // Retry restores the happy path.
    server.use(
      http.get(
        `${import.meta.env.VITE_API_BASE_URL}/api/analytics/ops`,
        () => HttpResponse.json(opsAnalytics)
      )
    );
    fireEvent.click(screen.getByTestId('ops-retry'));
    await waitFor(() =>
      expect(screen.getByTestId('ops-analytics')).toBeInTheDocument()
    );
  });
});
