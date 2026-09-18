// Dashboard component test — the MSW-intercepted happy path plus the explicit
// error state when the stats API fails (the dashboard never renders fake
// zeros; failure is shown with a retry).
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import Dashboard from './Dashboard';
import { server, dashboardStats } from '../test/handlers';

function renderDashboard(props = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Dashboard {...props} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

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

  it('shows an explicit error state with retry when the stats API fails', async () => {
    server.use(
      http.get(
        `${import.meta.env.VITE_API_BASE_URL}/api/dashboard/stats`,
        () => HttpResponse.json({ detail: 'database unavailable' }, { status: 500 })
      )
    );

    renderDashboard();

    await waitFor(() =>
      expect(screen.getByTestId('dashboard-error')).toBeInTheDocument()
    );

    // The failure is visible and actionable, not silently zeroed.
    expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
    expect(screen.getByTestId('dashboard-retry')).toBeInTheDocument();
  });

  it('pushes recent claims to the parent through onRecentClaims', async () => {
    const onRecentClaims = vi.fn();
    renderDashboard({ onRecentClaims });

    await waitFor(() =>
      expect(onRecentClaims).toHaveBeenCalledWith(dashboardStats.recentClaims)
    );
  });
});
