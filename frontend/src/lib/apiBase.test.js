// Unit tests for the shared API base-URL resolver.
//
// The workbench SSE stream used to build `new URL(`${API_BASE}${path}`)` with
// no base argument, which throws for the relative `/api` base that same-origin
// builds produce (vite dev proxy, nginx web tier) — the stream then degraded
// to polling. These tests pin the resolver contract for every deploy shape.
import { describe, it, expect, vi } from 'vitest';
import { resolveApiBase, buildApiUrl } from './apiBase';

describe('resolveApiBase', () => {
  it('returns the relative /api base for same-origin deploys (empty VITE_API_BASE_URL)', () => {
    expect(resolveApiBase('')).toBe('/api');
    expect(resolveApiBase('   ')).toBe('/api');
    // An unset build-time base is the nginx web-tier shape: /api is proxied
    // by the serving origin.
    vi.stubEnv('VITE_API_BASE_URL', '');
    try {
      expect(resolveApiBase()).toBe('/api');
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it('appends /api to the build-time absolute base (dev and split-origin deploys)', () => {
    expect(resolveApiBase('http://localhost:8001')).toBe('http://localhost:8001/api');
    expect(resolveApiBase('https://api.claimos.example')).toBe('https://api.claimos.example/api');
  });

  it('trims trailing slashes so the /api prefix never doubles up', () => {
    expect(resolveApiBase('http://localhost:8001/')).toBe('http://localhost:8001/api');
    expect(resolveApiBase('http://localhost:8001//')).toBe('http://localhost:8001/api');
    expect(resolveApiBase('https://api.claimos.example/')).toBe('https://api.claimos.example/api');
  });

  it('accepts a base that already ends in /api without doubling the prefix', () => {
    expect(resolveApiBase('https://api.claimos.example/api')).toBe('https://api.claimos.example/api');
    expect(resolveApiBase('https://api.claimos.example/api/')).toBe('https://api.claimos.example/api');
  });
});

describe('buildApiUrl', () => {
  const ORIGIN = 'http://web-tier:3000';

  it('resolves a relative (same-origin) base against the page origin — the production nginx case', () => {
    expect(buildApiUrl('/workbench/stream', undefined, { base: '/api', origin: ORIGIN })).toBe(
      'http://web-tier:3000/api/workbench/stream'
    );
  });

  it('uses the build-time absolute base verbatim (dev / split-origin deploys)', () => {
    expect(
      buildApiUrl('/workbench/stream', undefined, {
        base: 'http://localhost:8001/api',
        origin: ORIGIN,
      })
    ).toBe('http://localhost:8001/api/workbench/stream');
  });

  it('serializes defined, non-empty params only', () => {
    expect(
      buildApiUrl(
        '/workbench/stream',
        { status: 'open', q: '', page: null, missing: undefined },
        { base: '/api', origin: ORIGIN }
      )
    ).toBe('http://web-tier:3000/api/workbench/stream?status=open');
  });

  it('composes base and path with /api appearing exactly once', () => {
    const url = buildApiUrl('/events/streams/CLM-1', undefined, {
      base: resolveApiBase('https://api.claimos.example/'),
      origin: ORIGIN,
    });
    expect(url).toBe('https://api.claimos.example/api/events/streams/CLM-1');
    expect(url).not.toContain('/api/api');
  });
});
