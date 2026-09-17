// App smoke test — mounts the real console shell. Dashboard's stats request
// is served by the MSW handler (see src/test/handlers.js), so this exercises
// routing, layout, and the data-loading path end to end without a backend.
import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import App from '@/App';

describe('App smoke', () => {
  it('renders the console shell and hydrates the dashboard from the stats API', async () => {
    render(<App />);

    // Sidebar mounts immediately; Dashboard shows its loading state first.
    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('dashboard-loading')).toBeInTheDocument();

    // Once /api/dashboard/stats resolves, the loaded dashboard replaces it.
    await waitFor(() =>
      expect(screen.getByTestId('dashboard')).toBeInTheDocument()
    );

    expect(screen.getByText('Operations Dashboard')).toBeInTheDocument();
    expect(screen.getByText('TOTAL CLAIMS')).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
  });
});
