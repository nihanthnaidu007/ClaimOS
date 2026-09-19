// Queue page tests: rendering, filters/sort wire format, SLA badge states,
// empty/error paths, and live-update wiring through the mocked SSE module.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { openWorkbenchStream } from '@/lib/workbenchStream';
import WorkbenchQueue from './WorkbenchQueue';
import { server, workbenchQueueRows } from '../../test/handlers';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;

const { navigateSpy } = vi.hoisted(() => ({ navigateSpy: vi.fn() }));
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useNavigate: () => navigateSpy };
});

vi.mock('@/lib/workbenchStream', () => ({
  openWorkbenchStream: vi.fn(() => vi.fn()),
}));

function renderQueue() {
  return render(
    <MemoryRouter>
      <WorkbenchQueue />
    </MemoryRouter>
  );
}

describe('WorkbenchQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    openWorkbenchStream.mockImplementation(() => vi.fn());
  });

  it('renders queue rows with severity and SLA badges', async () => {
    renderQueue();

    await waitFor(() => expect(screen.getByTestId('workbench-queue')).toBeInTheDocument());
    expect(screen.getByTestId('queue-table')).toBeInTheDocument();
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument();
    expect(screen.getByText('Alan Turing')).toBeInTheDocument();

    const badges = screen.getAllByTestId('sla-badge');
    expect(badges).toHaveLength(2);
    expect(badges[0]).toHaveAttribute('data-state', 'breached');
    expect(badges[0].className).toContain('#ef4444'); // red for breaches
    expect(badges[1]).toHaveAttribute('data-state', 'ok');
  });

  it('renders the canonical empty state when no claims await review', async () => {
    server.use(
      http.get(`${API_BASE}/workbench/queue`, () =>
        HttpResponse.json({ rows: [], generatedAt: '2026-09-17T10:00:00+00:00' })
      )
    );
    renderQueue();

    await waitFor(() => expect(screen.getByTestId('queue-empty')).toBeInTheDocument());
    expect(screen.getByText('No claims awaiting review.')).toBeInTheDocument();
    expect(screen.queryByTestId('queue-table')).not.toBeInTheDocument();
  });

  it('shows an actionable error state with a working retry', async () => {
    server.use(
      http.get(`${API_BASE}/workbench/queue`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 })
      )
    );
    renderQueue();

    await waitFor(() => expect(screen.getByTestId('queue-error')).toBeInTheDocument());

    server.use(
      http.get(`${API_BASE}/workbench/queue`, () =>
        HttpResponse.json({ rows: workbenchQueueRows, generatedAt: '2026-09-17T10:00:00+00:00' })
      )
    );
    fireEvent.click(screen.getByTestId('queue-retry'));
    await waitFor(() => expect(screen.getByTestId('queue-table')).toBeInTheDocument());
    expect(screen.queryByTestId('queue-error')).not.toBeInTheDocument();
  });

  it('sends status, severity, age, and sort filters as query params', async () => {
    const capturedUrls = [];
    server.use(
      http.get(`${API_BASE}/workbench/queue`, ({ request }) => {
        capturedUrls.push(request.url);
        return HttpResponse.json({
          rows: workbenchQueueRows,
          generatedAt: '2026-09-17T10:00:00+00:00',
        });
      })
    );
    renderQueue();
    await waitFor(() => expect(capturedUrls.length).toBeGreaterThanOrEqual(1));

    fireEvent.change(screen.getByTestId('filter-status'), { target: { value: 'escalated' } });
    fireEvent.change(screen.getByTestId('filter-severity'), { target: { value: 'elevated' } });
    fireEvent.change(screen.getByTestId('filter-min-age'), { target: { value: '4' } });
    fireEvent.change(screen.getByTestId('filter-max-age'), { target: { value: '72' } });
    fireEvent.change(screen.getByTestId('filter-sort'), { target: { value: 'severity' } });

    await waitFor(() => {
      const last = new URL(capturedUrls[capturedUrls.length - 1]);
      expect(last.searchParams.get('status')).toBe('escalated');
      expect(last.searchParams.get('severity')).toBe('elevated');
      expect(last.searchParams.get('min_age_hours')).toBe('4');
      expect(last.searchParams.get('max_age_hours')).toBe('72');
      expect(last.searchParams.get('sort')).toBe('severity');
    });
  });

  it('debounces the search box before sending the search param', async () => {
    const capturedUrls = [];
    server.use(
      http.get(`${API_BASE}/workbench/queue`, ({ request }) => {
        capturedUrls.push(request.url);
        return HttpResponse.json({
          rows: workbenchQueueRows,
          generatedAt: '2026-09-17T10:00:00+00:00',
        });
      })
    );
    renderQueue();
    await waitFor(() => expect(capturedUrls.length).toBeGreaterThanOrEqual(1));
    const requestCountBeforeTyping = capturedUrls.length;

    fireEvent.change(screen.getByTestId('filter-search'), { target: { value: 'grace' } });

    // Debounced: no request goes out while the keystroke is fresh.
    expect(capturedUrls.length).toBe(requestCountBeforeTyping);

    // Once typing pauses, the query carries the search term.
    await waitFor(() => {
      const last = new URL(capturedUrls[capturedUrls.length - 1]);
      expect(last.searchParams.get('search')).toBe('grace');
    });
  });

  it('drops the search param when the search box is emptied', async () => {
    const capturedUrls = [];
    server.use(
      http.get(`${API_BASE}/workbench/queue`, ({ request }) => {
        capturedUrls.push(request.url);
        return HttpResponse.json({
          rows: workbenchQueueRows,
          generatedAt: '2026-09-17T10:00:00+00:00',
        });
      })
    );
    renderQueue();
    const searchBox = screen.getByTestId('filter-search');

    fireEvent.change(searchBox, { target: { value: 'grace' } });
    await waitFor(() => {
      const last = new URL(capturedUrls[capturedUrls.length - 1]);
      expect(last.searchParams.get('search')).toBe('grace');
    });

    fireEvent.change(searchBox, { target: { value: '' } });
    await waitFor(() => {
      const last = new URL(capturedUrls[capturedUrls.length - 1]);
      expect(last.searchParams.get('search')).toBeNull();
    });
  });

  it('renders row-shaped skeletons while the queue loads', () => {
    server.use(
      http.get(`${API_BASE}/workbench/queue`, () => new Promise(() => {})) // never resolves
    );
    renderQueue();

    const loading = screen.getByTestId('queue-loading');
    expect(loading).toHaveAttribute('aria-busy', 'true');
    expect(loading.children).toHaveLength(4); // skeleton rows at final row height
    expect(screen.queryByTestId('queue-table')).not.toBeInTheDocument();
  });

  it('shows the no-matches empty state with a working clear action', async () => {
    server.use(
      http.get(`${API_BASE}/workbench/queue`, () =>
        HttpResponse.json({ rows: [], generatedAt: '2026-09-17T10:00:00+00:00' })
      )
    );
    renderQueue();
    await waitFor(() => expect(screen.getByTestId('queue-empty')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('filter-search'), { target: { value: 'zzz' } });
    await waitFor(() => expect(screen.getByTestId('queue-empty-search')).toBeInTheDocument());
    expect(screen.getByText('No claims match your search.')).toBeInTheDocument();
    expect(screen.queryByTestId('queue-empty')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('clear-search'));
    await waitFor(() => expect(screen.getByTestId('queue-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('queue-empty-search')).not.toBeInTheDocument();
    expect(screen.getByTestId('filter-search').value).toBe('');
  });

  it('opens the live queue stream with the active filters and reports state', async () => {
    openWorkbenchStream.mockImplementation(({ onStatus }) => {
      onStatus?.('live');
      return vi.fn();
    });
    renderQueue();

    await waitFor(() =>
      expect(screen.getByTestId('live-indicator')).toHaveAttribute('data-status', 'live')
    );
    expect(openWorkbenchStream).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/workbench/stream',
        onMessage: expect.any(Function),
        onStatus: expect.any(Function),
      })
    );
  });

  it('replaces rows from a queue_update stream frame', async () => {
    let pushFrame;
    openWorkbenchStream.mockImplementation(({ onMessage, onStatus }) => {
      pushFrame = onMessage;
      onStatus?.('live');
      return vi.fn();
    });
    renderQueue();
    await screen.findByTestId('workbench-queue');

    pushFrame({
      event: 'queue_update',
      rows: [
        {
          id: 'CLM-2000',
          holder_name: 'Streaming Update',
          claimed_amount: 10,
          status: 'pending',
          severity: 'low',
          sla: { state: 'at_risk', breached: false, hoursRemaining: 3.5, hoursElapsed: 20.5 },
        },
      ],
    });

    await waitFor(() => expect(screen.getByText('Streaming Update')).toBeInTheDocument());
    expect(screen.getByTestId('sla-badge')).toHaveAttribute('data-state', 'at_risk');
  });

  it('ignores stream frames that are not queue_update events', async () => {
    let pushFrame;
    openWorkbenchStream.mockImplementation(({ onMessage, onStatus }) => {
      pushFrame = onMessage;
      onStatus?.('live');
      return vi.fn();
    });
    renderQueue();
    await screen.findByTestId('workbench-queue');

    pushFrame({ event: 'unknown_event', rows: [{ id: 'CLM-9999' }] });
    expect(screen.queryByText('CLM-9999')).not.toBeInTheDocument();
  });
});

// ---- F12: bulk actions ----
describe('WorkbenchQueue bulk actions', () => {
  it('hides the bulk bar until a row is selected and enables select-all', async () => {
    renderQueue();
    await screen.findByTestId('queue-table');
    expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('select-row-CLM-1001'));
    expect(screen.getByTestId('bulk-bar')).toBeInTheDocument();
    expect(screen.getByTestId('bulk-selected-count')).toHaveTextContent('1 selected');

    fireEvent.click(screen.getByTestId('select-all'));
    expect(screen.getByTestId('bulk-selected-count')).toHaveTextContent('2 selected');

    fireEvent.click(screen.getByTestId('select-all'));
    expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument();
  });

  it('keeps row navigation separate from checkbox selection', async () => {
    renderQueue();
    await screen.findByTestId('queue-table');

    fireEvent.click(screen.getByTestId('select-row-CLM-1002'));
    expect(navigateSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('bulk-selected-count')).toHaveTextContent('1 selected');
  });

  it('confirms the N-claim count and disables apply until a reason is entered', async () => {
    renderQueue();
    await screen.findByTestId('queue-table');

    fireEvent.click(screen.getByTestId('select-all'));
    fireEvent.click(screen.getByTestId('bulk-flag-button'));

    expect(screen.getByTestId('bulk-modal')).toBeInTheDocument();
    expect(screen.getByTestId('bulk-claim-count-n')).toHaveTextContent('2');
    expect(screen.getByTestId('bulk-confirm')).toBeDisabled();

    fireEvent.change(screen.getByTestId('bulk-reason'), { target: { value: 'Suspicious invoices' } });
    expect(screen.getByTestId('bulk-confirm')).toBeEnabled();
  });

  it('applies a bulk flag and reports the outcome', async () => {
    const bulkPosts = [];
    server.use(
      http.post(`${API_BASE}/workbench/claims/bulk`, async ({ request }) => {
        bulkPosts.push(await request.json());
        return HttpResponse.json({
          action: 'flag',
          results: [
            { claimId: 'CLM-1001', status: 'updated', auditId: 'audit-1' },
            { claimId: 'CLM-1002', status: 'updated', auditId: 'audit-2' },
          ],
          updated: 2,
          failed: 0,
        });
      })
    );
    renderQueue();
    await screen.findByTestId('queue-table');

    fireEvent.click(screen.getByTestId('select-all'));
    fireEvent.click(screen.getByTestId('bulk-flag-button'));
    fireEvent.change(screen.getByTestId('bulk-reason'), { target: { value: 'Suspicious invoices' } });
    fireEvent.click(screen.getByTestId('bulk-confirm'));

    await screen.findByTestId('bulk-result');
    expect(bulkPosts[0]).toEqual({
      action: 'flag',
      claimIds: ['CLM-1001', 'CLM-1002'],
      reason: 'Suspicious invoices',
    });
    expect(screen.getByTestId('bulk-result')).toHaveTextContent('Flagged 2 claims.');
    expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument(); // selection cleared
  });

  it('reports per-claim failures instead of dropping them', async () => {
    server.use(
      http.post(`${API_BASE}/workbench/claims/bulk`, () =>
        HttpResponse.json({
          action: 'flag',
          results: [
            { claimId: 'CLM-1001', status: 'updated', auditId: 'audit-1' },
            { claimId: 'CLM-1002', status: 'failed', detail: 'Claim not found' },
          ],
          updated: 1,
          failed: 1,
        })
      )
    );
    renderQueue();
    await screen.findByTestId('queue-table');

    fireEvent.click(screen.getByTestId('select-all'));
    fireEvent.click(screen.getByTestId('bulk-flag-button'));
    fireEvent.change(screen.getByTestId('bulk-reason'), { target: { value: 'Mixed batch' } });
    fireEvent.click(screen.getByTestId('bulk-confirm'));

    await screen.findByTestId('bulk-result-failures');
    expect(screen.getByTestId('bulk-result-failures')).toHaveTextContent('1 failed:');
    expect(screen.getByTestId('bulk-result-failures')).toHaveTextContent('CLM-1002 — Claim not found');
  });

  it('requires a target adjuster for bulk reassign and posts it', async () => {
    const bulkPosts = [];
    server.use(
      http.post(`${API_BASE}/workbench/claims/bulk`, async ({ request }) => {
        bulkPosts.push(await request.json());
        return HttpResponse.json({
          action: 'reassign',
          results: [{ claimId: 'CLM-1001', status: 'updated', auditId: 'audit-1' }],
          updated: 1,
          failed: 0,
        });
      })
    );
    renderQueue();
    await screen.findByTestId('queue-table');

    fireEvent.click(screen.getByTestId('select-row-CLM-1001'));
    fireEvent.click(screen.getByTestId('bulk-reassign-button'));
    fireEvent.change(screen.getByTestId('bulk-reason'), { target: { value: 'Vacation hand-off' } });
    expect(screen.getByTestId('bulk-confirm')).toBeDisabled(); // no target yet

    fireEvent.change(screen.getByTestId('bulk-target'), { target: { value: 'ops@claimos.dev' } });
    fireEvent.click(screen.getByTestId('bulk-confirm'));

    await screen.findByTestId('bulk-result');
    expect(bulkPosts[0]).toEqual({
      action: 'reassign',
      claimIds: ['CLM-1001'],
      reason: 'Vacation hand-off',
      target: 'ops@claimos.dev',
    });
    expect(screen.getByTestId('bulk-result')).toHaveTextContent('Reassigned 1 claim.');
  });

  it('surfaces bulk failures with the server detail and keeps the modal open', async () => {
    server.use(
      http.post(`${API_BASE}/workbench/claims/bulk`, () =>
        HttpResponse.json({ detail: 'Target adjuster not found: ops@claimos.dev' }, { status: 404 })
      )
    );
    renderQueue();
    await screen.findByTestId('queue-table');

    fireEvent.click(screen.getByTestId('select-row-CLM-1001'));
    fireEvent.click(screen.getByTestId('bulk-reassign-button'));
    fireEvent.change(screen.getByTestId('bulk-reason'), { target: { value: 'Hand-off' } });
    fireEvent.change(screen.getByTestId('bulk-target'), { target: { value: 'ops@claimos.dev' } });
    fireEvent.click(screen.getByTestId('bulk-confirm'));

    await screen.findByTestId('bulk-error');
    expect(screen.getByTestId('bulk-error')).toHaveTextContent('Target adjuster not found: ops@claimos.dev');
    expect(screen.getByTestId('bulk-modal')).toBeInTheDocument();
    expect(screen.queryByTestId('bulk-result')).not.toBeInTheDocument();
  });
});

// ---- F12: saved views ----
describe('WorkbenchQueue saved views', () => {
  const savedView = {
    id: 'vw_1',
    name: 'Elevated invoices',
    filters: { severity: 'elevated', sort: 'age' },
    createdAt: '2026-09-18T09:00:00+00:00',
  };

  it('saves the current filters as a named view and lists it as a chip', async () => {
    const viewPosts = [];
    server.use(
      http.post(`${API_BASE}/workbench/views`, async ({ request }) => {
        viewPosts.push(await request.json());
        return HttpResponse.json({ ...savedView, id: 'vw_new1' }, { status: 201 });
      }),
      http.get(`${API_BASE}/workbench/views`, () =>
        HttpResponse.json([{ ...savedView, id: 'vw_new1' }])
      )
    );
    renderQueue();
    await screen.findByTestId('queue-table');

    fireEvent.change(screen.getByTestId('filter-severity'), { target: { value: 'elevated' } });
    fireEvent.click(screen.getByTestId('save-view-button'));
    fireEvent.change(screen.getByTestId('view-name-input'), { target: { value: 'Elevated invoices' } });
    fireEvent.click(screen.getByTestId('view-save-confirm'));

    await screen.findByTestId('view-chip-vw_new1');
    expect(viewPosts[0]).toEqual({
      name: 'Elevated invoices',
      filters: { severity: 'elevated', sort: 'age' },
    });
  });

  it('applies a saved view: filters and rows update from the server response', async () => {
    server.use(
      http.get(`${API_BASE}/workbench/views`, () => HttpResponse.json([savedView])),
      http.get(`${API_BASE}/workbench/views/vw_1/apply`, () =>
        HttpResponse.json({
          view: savedView,
          rows: [workbenchQueueRows[0]],
          generatedAt: '2026-09-18T09:00:00+00:00',
        })
      )
    );
    renderQueue();
    await screen.findByTestId('queue-table');

    fireEvent.click(screen.getByTestId('view-chip-vw_1'));

    await waitFor(() => expect(screen.getByTestId('filter-severity')).toHaveValue('elevated'));
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument();
    expect(screen.queryByText('Alan Turing')).not.toBeInTheDocument();
  });

  it('deletes a saved view and drops its chip', async () => {
    server.use(http.get(`${API_BASE}/workbench/views`, () => HttpResponse.json([savedView])));
    renderQueue();
    await screen.findByTestId('view-chip-vw_1');

    server.use(http.get(`${API_BASE}/workbench/views`, () => HttpResponse.json([])));
    fireEvent.click(screen.getByTestId('view-delete-vw_1'));

    await waitFor(() => expect(screen.queryByTestId('view-chip-vw_1')).not.toBeInTheDocument());
  });
});
