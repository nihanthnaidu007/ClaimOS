// Authenticated API client.
//
// The backend holds nothing in localStorage: the access token lives in memory
// only, and the rotating refresh token comes back as an httpOnly cookie scoped
// to /api/auth. Refreshing requires the session-bound CSRF cookie value in the
// X-CSRF-Token header (double-submit), so refresh is only possible from this
// origin.

import axios from 'axios';
import { resolveApiBase } from './apiBase';

export const API_BASE = resolveApiBase();

let accessToken = null;
let refreshPromise = null;
let unauthorizedHandler = null;

export function getAccessToken() {
  return accessToken;
}

export function setAccessToken(token) {
  accessToken = token;
}

// Registered by the AuthProvider: fires when a refresh attempt also fails, so
// the UI can fall back to the login gate. api.js stays provider-agnostic.
export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
}

export function readCsrfCookie() {
  const match = document.cookie.match(/(?:^|;\s*)claimos_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : '';
}

async function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = axios
      .post(`${API_BASE}/auth/refresh`, null, {
        withCredentials: true,
        headers: { 'X-CSRF-Token': readCsrfCookie() },
      })
      .then((res) => {
        accessToken = res.data.accessToken;
        return res.data;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

export { refreshSession };

const api = axios.create({ baseURL: API_BASE, withCredentials: true });

api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

// 401 → one silent refresh + retry; a second 401 (or a failed refresh) falls
// through to the registered unauthorized handler. Auth endpoints never retry —
// a 401 there IS the signal (wrong password, expired refresh).
api.interceptors.response.use(null, async (error) => {
  const original = error.config || {};
  const isAuthRoute = String(original.url || '').includes('/auth/');
  if (error.response?.status === 401 && !original._retried && !isAuthRoute) {
    original._retried = true;
    try {
      await refreshSession();
      return api(original);
    } catch {
      accessToken = null;
      if (unauthorizedHandler) unauthorizedHandler();
    }
  }
  throw error;
});

export default api;
