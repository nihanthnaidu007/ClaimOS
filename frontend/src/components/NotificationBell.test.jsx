// NotificationBell component test — sign-in inside the bell, milestone list
// rendering with unread badge, mark-all-read, and the failure/empty states.
// All HTTP is MSW-intercepted (onUnhandledRequest: 'error' in setup.js).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import NotificationBell from './NotificationBell';
import { server } from '../test/handlers';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;

const notifications = [
  {
    id: 'ntf_1',
    claimId: 'CLM-20260917-042',
    milestone: 'submitted',
    title: 'Claim submitted',
    body: 'Your claim was received and queued for processing.',
    read: false,
    createdAt: '2026-09-17T10:00:00+00:00',
  },
  {
    id: 'ntf_2',
    claimId: 'CLM-20260917-042',
    milestone: 'decision_ready',
    title: 'Decision ready',
    body: 'A decision has been reached on your claim.',
    read: true,
    createdAt: '2026-09-17T11:00:00+00:00',
  },
];

const loginOk = () =>
  server.use(
    http.post(`${API_BASE}/auth/login`, () =>
      HttpResponse.json({ accessToken: 'test-token', tokenType: 'bearer' })
    )
  );
const listOk = (items = notifications, unread = 1) =>
  server.use(
    http.get(`${API_BASE}/notifications`, () =>
      HttpResponse.json({ notifications: items, unreadCount: unread })
    )
  );

const openBell = () => {
  render(<NotificationBell />);
  fireEvent.click(screen.getByTestId('notification-bell'));
};

const signIn = () => {
  fireEvent.change(screen.getByTestId('bell-signin-email'), {
    target: { value: 'sam@example.com' },
  });
  fireEvent.change(screen.getByTestId('bell-signin-password'), {
    target: { value: 'correct-horse' },
  });
  fireEvent.submit(screen.getByTestId('bell-signin-submit'));
};

describe('NotificationBell', () => {
  beforeEach(() => {
    // The component surfaces failures in-UI; keep test output clean.
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('opens a panel with an inline sign-in when unauthenticated', () => {
    openBell();
    expect(screen.getByTestId('notification-panel')).toBeInTheDocument();
    expect(screen.getByTestId('bell-signin-email')).toBeInTheDocument();
    expect(screen.getByTestId('bell-signin-password')).toBeInTheDocument();
  });

  it('signs in, lists notifications, and shows the unread badge', async () => {
    loginOk();
    listOk(notifications, 1);
    openBell();
    signIn();

    await waitFor(() =>
      expect(screen.getAllByTestId('notification-item')).toHaveLength(2)
    );
    expect(screen.getByText('Claim submitted')).toBeInTheDocument();
    expect(screen.getByTestId('notification-badge')).toHaveTextContent('1');
    expect(screen.getByTestId('notification-mark-read')).toBeInTheDocument();
  });

  it('marks all read and clears the badge', async () => {
    loginOk();
    listOk(notifications, 1);
    let markedIds = null;
    server.use(
      http.post(`${API_BASE}/notifications/mark-read`, async (req) => {
        markedIds = await req.request.json();
        return HttpResponse.json({ markedRead: markedIds.ids.length });
      })
    );
    openBell();
    signIn();
    await waitFor(() => screen.getByTestId('notification-mark-read'));

    fireEvent.click(screen.getByTestId('notification-mark-read'));
    await waitFor(() =>
      expect(screen.queryByTestId('notification-badge')).not.toBeInTheDocument()
    );
    expect(markedIds).toEqual({ ids: ['ntf_1'] }); // only the unread one
  });

  it('shows the empty state when the customer has no notifications', async () => {
    loginOk();
    listOk([], 0);
    openBell();
    signIn();

    await waitFor(() =>
      expect(screen.getByTestId('notification-empty')).toBeInTheDocument()
    );
    expect(screen.queryByTestId('notification-badge')).not.toBeInTheDocument();
  });

  it('shows an error when sign-in fails', async () => {
    server.use(
      http.post(`${API_BASE}/auth/login`, () =>
        HttpResponse.json({ detail: 'Invalid credentials' }, { status: 401 })
      )
    );
    openBell();
    signIn();

    await waitFor(() =>
      expect(screen.getByTestId('notification-error')).toHaveTextContent(
        'Sign-in failed'
      )
    );
  });

  it('shows an error when the notification list fails to load', async () => {
    loginOk();
    server.use(
      http.get(`${API_BASE}/notifications`, () =>
        HttpResponse.json({ detail: 'database unavailable' }, { status: 500 })
      )
    );
    openBell();
    signIn();

    await waitFor(() =>
      expect(screen.getByTestId('notification-error')).toHaveTextContent(
        'Notifications could not be loaded'
      )
    );
  });
});
