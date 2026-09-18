// usePipelineEvents tests — the SSE wire → cache chain.
//
// Pins the contract the FNOL submission view depends on: a clean server
// close (the durable stream terminates right after a terminal event) must
// invalidate the claim queries so the refetched record renders the decision
// panel even when the terminal frame itself arrives last. Regression for
// the 2026-09-18 incident where a render crash upstream killed the page
// before the close-handling could refetch — and for any future regression
// in the close → invalidate → refetch path itself.
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../test/handlers';
import { usePipelineEvents } from './usePipelineEvents';
import { useClaim } from '../lib/queries';

function sseFrame(id, payload) {
  return `id: ${id}\ndata: ${JSON.stringify(payload)}\n\n`;
}

// The durable stream: two frames then a clean server close — exactly what
// the backend serves once the run settles (tail_claim_events returns right
// after the terminal event).
const closedStream = http.get('*/api/events/streams/CLM-TEST', () => {
  const body = new ReadableStream({
    start(controller) {
      const enc = new TextEncoder();
      controller.enqueue(
        enc.encode(sseFrame(1, { event: 'claim_submitted', claim_id: 'CLM-TEST' }))
      );
      controller.enqueue(
        enc.encode(
          sseFrame(2, {
            event: 'claim_failed',
            reason: 'Agent ELIGIBILITY_AGENT failed',
          })
        )
      );
      controller.close();
    },
  });
  return new HttpResponse(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
});

// The claim record: pending on the first fetch (at submission), the settled
// server truth after the stream-close invalidation refetches.
let claimFetches = 0;
const claimRecord = http.get('*/api/claims/CLM-TEST', () => {
  claimFetches += 1;
  return HttpResponse.json({
    id: 'CLM-TEST',
    status: claimFetches === 1 ? 'pending' : 'failed',
  });
});

function Probe() {
  const { connectionState } = usePipelineEvents({
    claimId: 'CLM-TEST',
    enabled: true,
    invalidateOnReconnect: ['claims'],
  });
  const { data } = useClaim('CLM-TEST');
  return (
    <div data-testid="probe">
      {connectionState}:{data?.status ?? 'none'}
    </div>
  );
}

function renderProbe() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Probe />
    </QueryClientProvider>
  );
}

describe('usePipelineEvents — clean stream close', () => {
  afterEach(() => {
    server.resetHandlers();
    cleanup();
  });

  it('refetches the claim record after the server closes the stream', async () => {
    server.use(closedStream, claimRecord);
    claimFetches = 0;

    renderProbe();

    // The stream ran to its clean close; the hook must have dropped to
    // offline AND invalidated, so the claim refetch returns server truth.
    await waitFor(
      () => expect(screen.getByTestId('probe')).toHaveTextContent('offline:failed'),
      { timeout: 3000 }
    );
    expect(claimFetches).toBeGreaterThanOrEqual(2);
  });
});
