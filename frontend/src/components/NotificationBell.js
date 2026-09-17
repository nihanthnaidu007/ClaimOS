import { useEffect, useRef, useState } from 'react';
import { Bell, MailCheck } from 'lucide-react';
import axios from 'axios';

const API = `${import.meta.env.VITE_API_BASE_URL}/api`;

// In-app notification center for the signed-in customer view. Backend auth
// requires a bearer access token; this component owns its own minimal inline
// sign-in so the bell is usable before the full auth provider lands.

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState(null);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const panelRef = useRef(null);

  const loadNotifications = async (authToken) => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/notifications`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      setNotifications(res.data.notifications);
      setUnreadCount(res.data.unreadCount);
      setError('');
    } catch {
      setError('Notifications could not be loaded.');
    } finally {
      setLoading(false);
    }
  };

  const signIn = async (e) => {
    e && e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await axios.post(`${API}/auth/login`, { email, password });
      setToken(res.data.accessToken);
      await loadNotifications(res.data.accessToken);
    } catch {
      setError('Sign-in failed. Check your email and password.');
    } finally {
      setLoading(false);
    }
  };

  const markAllRead = async () => {
    const unreadIds = notifications.filter((n) => !n.read).map((n) => n.id);
    if (unreadIds.length === 0) return;
    try {
      await axios.post(`${API}/notifications/mark-read`, { ids: unreadIds }, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
      setUnreadCount(0);
    } catch {
      setError('Could not mark notifications as read.');
    }
  };

  // Close the panel on outside click.
  useEffect(() => {
    if (!open) return undefined;
    const onClick = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  const signedIn = Boolean(token);

  return (
    <div className="relative" ref={panelRef}>
      <button
        type="button"
        data-testid="notification-bell"
        aria-label={signedIn ? `Notifications (${unreadCount} unread)` : 'Sign in to see notifications'}
        onClick={() => setOpen((prev) => !prev)}
        className="relative flex items-center justify-center w-9 h-9 border border-[#1a1f2e] hover:border-[#3b82f6] rounded-sm transition-colors duration-200"
      >
        <Bell className="w-4 h-4 text-[#8892a4]" />
        {signedIn && unreadCount > 0 && (
          <span
            data-testid="notification-badge"
            className="absolute -top-1.5 -right-1.5 bg-[#ef4444] text-white text-[10px] font-mono px-1.5 py-0.5 rounded-full"
          >
            {unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div
          data-testid="notification-panel"
          className="absolute bottom-full right-0 mb-2 w-80 bg-[#0f1218] border border-[#1a1f2e] shadow-xl z-50"
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#1a1f2e]">
            <span className="text-xs uppercase tracking-wider text-[#8892a4] font-mono">Notifications</span>
            {signedIn && unreadCount > 0 && (
              <button
                type="button"
                data-testid="notification-mark-read"
                onClick={markAllRead}
                className="flex items-center gap-1 text-xs text-[#3b82f6] hover:text-[#60a5fa] font-mono transition-colors duration-200"
              >
                <MailCheck className="w-3.5 h-3.5" /> Mark all read
              </button>
            )}
          </div>

          {!signedIn && (
            <form onSubmit={signIn} className="p-4">
              <p className="text-[12px] text-[#8892a4] mb-3">
                Sign in to see your claim notifications.
              </p>
              <input
                type="email"
                data-testid="bell-signin-email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email"
                className="w-full bg-[#0a0c12] border border-[#1a1f2e] px-3 py-2 text-sm text-[#e2e8f0] placeholder-[#2a3040] focus:outline-none focus:border-[#3b82f6] mb-2"
              />
              <input
                type="password"
                data-testid="bell-signin-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
                className="w-full bg-[#0a0c12] border border-[#1a1f2e] px-3 py-2 text-sm text-[#e2e8f0] placeholder-[#2a3040] focus:outline-none focus:border-[#3b82f6] mb-3"
              />
              <button
                type="submit"
                data-testid="bell-signin-submit"
                disabled={loading || !email.trim() || !password}
                className="w-full bg-[#3b82f6] hover:bg-[#3b82f6]/90 disabled:opacity-40 text-white rounded-sm text-sm font-medium px-4 py-2 transition-colors duration-200"
              >
                {loading ? 'Signing in…' : 'Sign in'}
              </button>
            </form>
          )}

          {signedIn && error && (
            <div data-testid="notification-error" className="px-4 py-3 text-[12px] text-[#fbbf24] border-b border-[#1a1f2e]">
              {error}
            </div>
          )}

          {signedIn && !error && loading && (
            <div data-testid="notification-loading" className="px-4 py-4 text-[12px] text-[#8892a4]">
              Loading…
            </div>
          )}

          {signedIn && !error && !loading && notifications.length === 0 && (
            <div data-testid="notification-empty" className="px-4 py-4 text-[12px] text-[#4a5568]">
              No notifications yet. Milestone updates for your claims will appear here.
            </div>
          )}

          {signedIn && !error && !loading && notifications.length > 0 && (
            <ul className="max-h-72 overflow-y-auto">
              {notifications.map((n) => (
                <li
                  key={n.id}
                  data-testid="notification-item"
                  className={`px-4 py-3 border-b border-[#1a1f2e] last:border-b-0 ${n.read ? '' : 'bg-[#3b82f6]/5'}`}
                >
                  <p className={`text-[13px] ${n.read ? 'text-[#8892a4]' : 'text-[#e2e8f0] font-medium'}`}>
                    {n.title}
                  </p>
                  <p className="text-[12px] text-[#8892a4] mt-0.5">{n.body}</p>
                  {n.createdAt && (
                    <p className="text-[11px] text-[#4a5568] font-mono mt-1">{n.createdAt}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
