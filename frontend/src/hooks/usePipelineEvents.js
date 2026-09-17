// Live pipeline events over a fetch-streamed SSE connection.
//
// The durable stream (GET /api/events/streams/{claimId}) requires the
// Authorization header, which native EventSource cannot send — so this hook
// speaks the SSE wire format over fetch itself: `id:` frames become the
// Last-Event-ID resume cursor, disconnects back off exponentially with jitter,
// and a reconnect invalidates the claim queries so the board refetches server
// truth for anything missed while dark.
//
// React 19 StrictMode double-invokes effects; the AbortController lives in
// the effect scope so the remount starts a clean connection.
import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { getAccessToken } from '@/lib/api';

const BACKEND_URL = import.meta.env.VITE_API_BASE_URL;
const INITIAL_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 15_000;

// Full jitter (uniform in [0, cap]) — spread reconnects instead of stampeding.
export function backoffDelay(attempt, random = Math.random) {
  const cap = Math.min(INITIAL_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS);
  return Math.round(random() * cap);
}

// Parse one SSE text chunk into complete frames, preserving any trailing
// partial frame as remainder for the next chunk. Comment lines (`: heartbeat`)
// are dropped, `id:` sets the frame's resume cursor, and `data:` lines
// accumulate (the SSE spec allows multiple data lines per frame).
export function parseSseChunk(text, { lastEventId = null } = {}) {
  const events = [];
  let cursor = lastEventId;
  let dataLines = [];
  let sawData = false;

  const flush = () => {
    if (sawData) {
      const raw = dataLines.join('\n');
      try {
        events.push({ id: cursor, data: JSON.parse(raw) });
      } catch {
        events.push({ id: cursor, data: { event: 'unknown', raw } });
      }
    }
    dataLines = [];
    sawData = false;
  };

  const segments = text.split('\n');
  const remainder = segments.pop() ?? '';
  for (const line of segments) {
    if (line === '') {
      flush();
      continue;
    }
    if (line.startsWith(':')) continue; // comment / heartbeat
    if (line.startsWith('id:')) {
      const value = line.slice(3).trim();
      cursor = value === '' ? null : Number(value);
      continue;
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart());
      sawData = true;
    }
  }
  return { events, remainder, lastEventId: cursor };
}

/**
 * Subscribe to a claim's durable event stream.
 *
 * @param {object} options
 * @param {string|null} options.claimId  Claim whose stream to tail.
 * @param {boolean} [options.enabled]
 * @param {(event: object) => void} [options.onEvent]  Receives each event's
 *   JSON payload (the flat wire shape: {event, ...}).
 * @param {string[]} [options.invalidateOnReconnect]  Query-key prefixes to
 *   invalidate when the connection drops, so refetched data covers the gap.
 * @returns {{ connectionState: string }} connecting | live | reconnecting | offline.
 */
export function usePipelineEvents({
  claimId,
  enabled = true,
  onEvent,
  invalidateOnReconnect = ['claims'],
} = {}) {
  const queryClient = useQueryClient();
  const [connectionState, setConnectionState] = useState('connecting');
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const lastEventIdRef = useRef(null);

  useEffect(() => {
    if (!enabled || !claimId) return undefined;

    const controller = new AbortController();
    let stopped = false;
    let timer = null;
    let attempt = 0;

    const connect = async () => {
      setConnectionState(attempt === 0 ? 'connecting' : 'reconnecting');
      try {
        const headers = { Accept: 'text/event-stream' };
        const token = getAccessToken();
        if (token) headers.Authorization = `Bearer ${token}`;
        if (lastEventIdRef.current !== null) {
          headers['Last-Event-ID'] = String(lastEventIdRef.current);
        }

        const res = await fetch(`${BACKEND_URL}/api/events/streams/${claimId}`, {
          headers,
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          throw new Error(`event stream returned ${res.status}`);
        }

        attempt = 0;
        setConnectionState('live');

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let cursor = lastEventIdRef.current ?? null;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseSseChunk(buffer, { lastEventId: cursor });
          buffer = parsed.remainder;
          cursor = parsed.lastEventId;
          lastEventIdRef.current = parsed.lastEventId;
          for (const frame of parsed.events) {
            onEventRef.current?.(frame.data);
          }
        }

        // The server closes the stream after a terminal event — that is a
        // normal end, not a failure.
        if (!stopped) setConnectionState('offline');
      } catch {
        if (stopped || controller.signal.aborted) return;
        attempt += 1;
        setConnectionState('reconnecting');
        for (const prefix of invalidateOnReconnect) {
          queryClient.invalidateQueries({ queryKey: [...prefix, claimId] });
        }
        timer = setTimeout(connect, backoffDelay(attempt));
      }
    };

    connect();

    return () => {
      stopped = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [claimId, enabled, queryClient, invalidateOnReconnect]);

  return { connectionState };
}
