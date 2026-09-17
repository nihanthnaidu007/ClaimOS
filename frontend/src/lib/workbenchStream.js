// Workbench stream: authenticated SSE over fetch.
//
// Native EventSource cannot set an Authorization header, so the queue stream
// uses fetch + a ReadableStream reader that parses the same wire format
// (`data: {...}` frames, `: comment` keep-alives). Auto-reconnects with
// backoff while a consumer is mounted; reports connection state so the UI can
// show a live/stale indicator instead of silently stale data.
import { API_BASE, getAccessToken } from './api';

function buildUrl(path, params) {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, value);
      }
    });
  }
  return url.toString();
}

function extractFramePayload(frame) {
  const dataLine = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .join('');
  if (!dataLine) return null;
  try {
    return JSON.parse(dataLine);
  } catch {
    // A malformed frame is skipped, not fatal — the next frame re-syncs.
    return null;
  }
}

export function openWorkbenchStream({ path, params, onMessage, onStatus }) {
  const controller = new AbortController();
  let stopped = false;
  let retryTimer = null;
  let attempt = 0;

  async function connect() {
    while (!stopped) {
      try {
        const response = await fetch(buildUrl(path, params), {
          headers: {
            Accept: 'text/event-stream',
            ...(getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {}),
          },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error(`stream request failed: ${response.status}`);
        }
        attempt = 0;
        onStatus?.('live');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split('\n\n');
          buffer = frames.pop() ?? '';
          frames.forEach((frame) => {
            const message = extractFramePayload(frame);
            if (message) onMessage(message);
          });
        }
        // Server closed the stream cleanly — reconnect unless stopped.
      } catch (error) {
        if (stopped || error?.name === 'AbortError') return;
        onStatus?.(attempt < 3 ? 'reconnecting' : 'error');
      }
      if (stopped) return;
      attempt += 1;
      const backoffMs = Math.min(15000, 1000 * 2 ** Math.min(attempt, 4));
      onStatus?.(attempt <= 3 ? 'reconnecting' : 'error');
      await new Promise((resolve) => {
        retryTimer = setTimeout(resolve, backoffMs);
      });
    }
  }

  connect();
  return () => {
    stopped = true;
    if (retryTimer) clearTimeout(retryTimer);
    controller.abort();
  };
}
