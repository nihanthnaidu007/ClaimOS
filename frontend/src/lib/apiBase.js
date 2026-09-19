// Single authority for API base-URL resolution.
//
// REST (api.js) and the SSE streams (workbenchStream.js, usePipelineEvents.js)
// must build identical URLs from one source of truth. VITE_API_BASE_URL is a
// build-time setting with two legitimate shapes:
//   - absolute origin (e.g. "http://localhost:8001") — API on another origin;
//   - empty/unset — same-origin deploys (vite dev proxy, nginx web tier),
//     where the serving origin proxies /api to the backend.
// Trailing slashes are trimmed and the "/api" prefix is applied exactly once,
// so callers always pass "/claims"-style paths — never "/api/...".

export function resolveApiBase(rawBase = import.meta.env.VITE_API_BASE_URL) {
  const trimmed = String(rawBase ?? '')
    .trim()
    .replace(/\/+$/, '')
    .replace(/\/api$/, ''); // a base that already ends in /api would otherwise double the prefix
  return `${trimmed}/api`;
}

// Resolve an API path (+ optional query params) to an absolute URL. Relative
// bases (same-origin deploys) resolve against the page origin — `new URL`
// requires that base argument, which is exactly what the SSE client was
// missing whenever VITE_API_BASE_URL was empty.
export function buildApiUrl(
  path,
  params,
  { base = resolveApiBase(), origin = window.location.origin } = {}
) {
  const url = new URL(`${base}${path}`, origin);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, value);
      }
    });
  }
  return url.toString();
}
