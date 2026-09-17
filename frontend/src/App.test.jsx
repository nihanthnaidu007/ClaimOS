// App smoke test — mounts the real console shell with the same providers
// index.js uses (QueryClient + AuthProvider). The MSW refresh handler grants a
// session, so RequireAuth lets the dashboard through; Dashboard's stats
// request is served by the handler in src/test/handlers.js, exercising
// routing, layout, and the data-loading path end to end without a backend.
import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from '@/App';
import { AuthProvider } from '@/lib/auth';

describe('App smoke', () => {
  it('renders the console shell and hydrates the dashboard from the stats API', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </QueryClientProvider>
    );

    // Auth restores silently, then the Dashboard shows its loading state and
    // finally the hydrated dashboard replaces it.
    await waitFor(() =>
      expect(screen.getByTestId('dashboard')).toBeInTheDocument()
    );

    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
    expect(screen.getByText('Operations Dashboard')).toBeInTheDocument();
    expect(screen.getByText('TOTAL CLAIMS')).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
  });
});
