// Dashboard component test — the MSW-intercepted happy path plus graceful
// degradation when the stats API fails.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import Dashboard from './Dashboard';
import { server, dashboardStats } from '../test/handlers';

const renderDashboard = (props = {}) =>
  render(
    <MemoryRouter>
      <Dashboard {...props} />
    </MemoryRouter>
  );

describe('Dashboard', () => {
  it('renders stat cards and recent claims from the mocked stats API', async () => {
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByTestId('dashboard')).toBeInTheDocument()
    );

    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('$48,250.75')).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    // "APPROVED" appears as both the stat-card label and the recent claim's
    // verdict pill.
    expect(screen.getAllByText('APPROVED')).toHaveLength(2);
  });

  it('falls back to zeroed state when the stats API errors', async () => {
    // Silence the component's console.error — the failure is the scenario
    // under test, not unexpected noise.
    vi.spyOn(console, 'error').mockImplementation(() => {});
    server.use(
      http.get(
        `${import.meta.env.VITE_API_BASE_URL}/api/dashboard/stats`,
        () => HttpResponse.json({ detail: 'database unavailable' }, { status: 500 })
      )
    );

    renderDashboard();

    await waitFor(() =>
      expect(screen.queryByTestId('dashboard-loading')).not.toBeInTheDocument()
    );

    expect(screen.getByTestId('dashboard')).toBeInTheDocument();
    // All four stat cards fall back to zero, plus the active-policies card
    // also renders "0" — five exact matches in total.
    expect(screen.getAllByText('0').length).toBeGreaterThanOrEqual(4);
    expect(
      screen.getByText(/No claims processed yet/i)
    ).toBeInTheDocument();
  });

  it('pushes recent claims to the parent through onRecentClaims', async () => {
    const onRecentClaims = vi.fn();
    renderDashboard({ onRecentClaims });

    await waitFor(() =>
      expect(onRecentClaims).toHaveBeenCalledWith(dashboardStats.recentClaims)
    );
  });
});
