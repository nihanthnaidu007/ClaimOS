// Session context: in-memory access token, silent refresh on mount, login gate.
//
// The provider owns three states: loading (refresh probe in flight),
// authenticated (token + user in memory), anonymous (show the login card).
// The backend has no /me endpoint, so mount-time session restore is a single
// POST /auth/refresh — cheap, and it makes a page reload survive the refresh
// cookie without persisting anything readable to scripts.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Loader2, LogIn } from 'lucide-react';
import api, { onUnauthorized, refreshSession, setAccessToken } from './api';

const AuthContext = createContext(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

export function AuthProvider({ children }) {
  const [status, setStatus] = useState('loading');
  const [user, setUser] = useState(null);

  useEffect(() => {
    const handleUnauthorized = () => {
      setAccessToken(null);
      setUser(null);
      setStatus('anonymous');
    };
    onUnauthorized(handleUnauthorized);

    let cancelled = false;
    refreshSession()
      .then((data) => {
        if (cancelled) return;
        setAccessToken(data.accessToken);
        setUser(data.user);
        setStatus('authenticated');
      })
      .catch(() => {
        if (cancelled) return;
        setStatus('anonymous');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password });
    setAccessToken(data.accessToken);
    setUser(data.user);
    setStatus('authenticated');
    return data.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout');
    } finally {
      setAccessToken(null);
      setUser(null);
      setStatus('anonymous');
    }
  }, []);

  const value = useMemo(() => ({ status, user, login, logout }), [status, user, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// Compact login card shown whenever the session is anonymous. Kept inside
// auth.js because it is the visual half of the same contract.
export function LoginGate() {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setPending(true);
    setError('');
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(err.response?.data?.detail || 'Login failed — try again.');
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="page-enter flex items-center justify-center h-screen" data-testid="login-gate">
      <form
        onSubmit={submit}
        className="w-full max-w-sm bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-6 space-y-4"
      >
        <div className="flex items-center gap-2.5">
          <LogIn className="w-5 h-5 text-[#3b82f6]" />
          <h1
            className="text-xl font-bold tracking-tight uppercase text-[#e2e8f0]"
            style={{ fontFamily: 'Space Grotesk' }}
          >
            ClaimOS Sign In
          </h1>
        </div>
        <div>
          <label htmlFor="login-email" className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] font-mono block mb-1.5">
            Email
          </label>
          <input
            id="login-email"
            data-testid="login-email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none px-3 py-2.5 text-sm font-mono focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] outline-none"
          />
        </div>
        <div>
          <label htmlFor="login-password" className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] font-mono block mb-1.5">
            Password
          </label>
          <input
            id="login-password"
            data-testid="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="w-full bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none px-3 py-2.5 text-sm font-mono focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] outline-none"
          />
        </div>
        {error && (
          <div className="text-xs text-[#ef4444] font-mono" data-testid="login-error" role="alert">
            {error}
          </div>
        )}
        <button
          type="submit"
          data-testid="login-submit"
          disabled={pending}
          className="w-full bg-[#3b82f6] hover:bg-[#3b82f6]/90 text-white rounded-none font-medium text-sm px-4 py-2.5 uppercase tracking-wide disabled:opacity-40 transition-colors duration-200 flex items-center justify-center gap-2"
        >
          {pending && <Loader2 className="w-4 h-4 animate-spin" />}
          Sign In
        </button>
      </form>
    </div>
  );
}

// Route-shell guard: hold a spinner while the refresh probe runs, then either
// the app or the login card. Never gates data security — the API enforces auth.
export function RequireAuth({ children }) {
  const { status } = useAuth();
  if (status === 'loading') {
    return (
      <div className="page-enter flex items-center justify-center h-screen text-[#4a5568]" data-testid="auth-loading">
        <Loader2 className="w-6 h-6 animate-spin" />
      </div>
    );
  }
  if (status === 'anonymous') return <LoginGate />;
  return children;
}
