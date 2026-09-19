// Session context: in-memory access token, silent refresh on mount, login gate.
//
// Three states: loading (refresh probe in flight), authenticated (token + user
// in memory), anonymous (show the login card). The backend has no /me endpoint,
// so mount-time session restore is a single POST /auth/refresh — cheap, and it
// makes a page reload survive the refresh cookie without persisting anything
// readable to scripts.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Loader2, LogIn, Hexagon } from 'lucide-react';
import api, { onUnauthorized, refreshSession, setAccessToken } from './api';

const AuthContext = createContext(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

export function AuthProvider({ children }) {
  const [state, setState] = useState({ status: 'loading', user: null });

  useEffect(() => {
    onUnauthorized(() => setState({ status: 'anonymous', user: null }));
    let cancelled = false;
    refreshSession()
      .then((data) => {
        if (!cancelled) setState({ status: 'authenticated', user: data.user });
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'anonymous', user: null });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password });
    setAccessToken(data.accessToken);
    setState({ status: 'authenticated', user: data.user });
    return data.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout');
    } finally {
      setAccessToken(null);
      setState({ status: 'anonymous', user: null });
    }
  }, []);

  const value = useMemo(() => ({ ...state, login, logout }), [state, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function LoginCard() {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
    } catch (err) {
      setError(
        err?.response?.status === 401 || err?.response?.status === 400
          ? 'Invalid email or password.'
          : 'Could not reach the sign-in service. Try again.'
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-[#0a0c12]" data-testid="login-gate">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-[#0d1119] border border-[#1a1f2e] rounded-xl p-8 space-y-4"
      >
        <div className="flex items-center gap-2.5 mb-2">
          <Hexagon className="w-6 h-6 text-[#3b82f6]" strokeWidth={2} />
          <span className="text-lg font-bold tracking-tight text-[#e2e8f0]" style={{ fontFamily: 'Space Grotesk' }}>
            ClaimOS
          </span>
        </div>
        <p className="text-sm text-[#8b96ab]">Adjuster sign-in for the workbench.</p>
        <label className="block text-xs uppercase tracking-wider text-[#8b96ab] font-mono" htmlFor="login-email">
          Email
          <input
            id="login-email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full bg-[#0a0c12] border border-[#1a1f2e] rounded-md px-3 py-2 text-sm text-[#e2e8f0] normal-case tracking-normal focus:outline-none focus:border-[#3b82f6]"
            autoComplete="username"
          />
        </label>
        <label className="block text-xs uppercase tracking-wider text-[#8b96ab] font-mono" htmlFor="login-password">
          Password
          <input
            id="login-password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full bg-[#0a0c12] border border-[#1a1f2e] rounded-md px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]"
            autoComplete="current-password"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-[#ef4444]" data-testid="login-error">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy}
          className="w-full inline-flex items-center justify-center gap-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm font-medium rounded-md px-4 py-2 transition-colors"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

/** Gate: renders children only for an authenticated user with `role`. */
export function RequireRole({ role, children }) {
  const { status, user } = useAuth();
  if (status === 'loading') {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[#0a0c12]" data-testid="auth-loading">
        <Loader2 className="w-6 h-6 animate-spin text-[#3b82f6]" />
      </div>
    );
  }
  if (status === 'anonymous') return <LoginCard />;
  if (role && user?.role !== role) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[#0a0c12]" data-testid="access-denied">
        <div className="text-center max-w-sm bg-[#0d1119] border border-[#1a1f2e] rounded-xl p-8">
          <h1 className="text-lg font-semibold text-[#e2e8f0]">Access denied</h1>
          <p className="mt-2 text-sm text-[#8b96ab]">
            This area is for {role}s. You are signed in as {user?.email} ({user?.role}).
          </p>
        </div>
      </div>
    );
  }
  return children;
}
