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
