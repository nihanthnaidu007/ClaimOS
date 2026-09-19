import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';

import MessageThreadPanel from './MessageThreadPanel';
import { server } from '../../test/handlers';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;
const claimId = 'CLM-MSG-1';
const threadUrl = `${API_BASE}/workbench/claims/${claimId}/messages`;

const thread = [
  {
    id: 'msg_1',
    claimId,
    authorRole: 'customer',
    authorId: 'portal-customer',
    body: 'Any update on my claim?',
    createdAt: '2026-09-18T10:00:00+00:00',
    readAt: null,
  },
  {
    id: 'msg_2',
    claimId,
    authorRole: 'adjuster',
    authorId: 'usr_adjuster',
    body: 'We received your photos and are reviewing them.',
    createdAt: '2026-09-18T10:05:00+00:00',
    readAt: null,
  },
];

function mockThread(messages = thread) {
  server.use(http.get(threadUrl, () => HttpResponse.json({ claimId, messages })));
}

describe('MessageThreadPanel (adjuster case view)', () => {
  afterEach(() => server.resetHandlers());

  it('shows an empty state before any messages exist', async () => {
    mockThread([]);
    render(<MessageThreadPanel claimId={claimId} />);
    expect(await screen.findByText(/no messages yet/i)).toBeInTheDocument();
  });

  it('renders the thread chronologically with role labels', async () => {
    mockThread(thread);
    render(<MessageThreadPanel claimId={claimId} />);
    expect(await screen.findByText('Any update on my claim?')).toBeInTheDocument();
    expect(screen.getByText('We received your photos and are reviewing them.')).toBeInTheDocument();
    expect(screen.getAllByText('Customer')).toHaveLength(1);
    expect(screen.getAllByText('Adjuster')).toHaveLength(1);
  });

  it('sends a message and refetches the thread', async () => {
    const user = userEvent.setup();
    const bodies = [];
    let listCalls = 0;
    server.use(
      http.get(threadUrl, () => {
        listCalls += 1;
        return HttpResponse.json({ claimId, messages: [] });
      }),
      http.post(threadUrl, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({}, { status: 201 });
      })
    );

    render(<MessageThreadPanel claimId={claimId} />);
    await screen.findByText(/no messages yet/i);

    await user.type(screen.getByLabelText(/message to the customer/i), 'Reply incoming');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    await waitFor(() => {
      expect(bodies).toEqual([{ body: 'Reply incoming' }]);
    });
    await waitFor(() => {
      expect(listCalls).toBe(2);
    });
  });

  it('renders hostile strings as inert text, never markup', async () => {
    mockThread([
      {
        ...thread[0],
        id: 'msg_hostile',
        body: '<script>alert("xss")</script><img src=x onerror=alert(1)><iframe src="https://evil.example"></iframe>',
      },
    ]);
    const { container } = render(<MessageThreadPanel claimId={claimId} />);

    expect(
      await screen.findByText(/<script>alert\("xss"\)<\/script>/)
    ).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('surfaces a rate-limit message on 429 and keeps the draft', async () => {
    const user = userEvent.setup();
    mockThread([]);
    server.use(http.post(threadUrl, () => new HttpResponse(null, { status: 429 })));
    render(<MessageThreadPanel claimId={claimId} />);
    await screen.findByText(/no messages yet/i);

    await user.type(screen.getByLabelText(/message to the customer/i), 'hello');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/too quickly/i);
    expect(screen.getByLabelText(/message to the customer/i)).toHaveValue('hello');
  });

  it('surfaces a length-cap message on 422', async () => {
    const user = userEvent.setup();
    mockThread([]);
    server.use(http.post(threadUrl, () => new HttpResponse(null, { status: 422 })));
    render(<MessageThreadPanel claimId={claimId} />);
    await screen.findByText(/no messages yet/i);

    await user.type(screen.getByLabelText(/message to the customer/i), 'x');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/between 1 and 2000/i);
  });

  it('offers a retry when the thread fails to load', async () => {
    mockThread([]);
    server.use(http.get(threadUrl, () => new HttpResponse(null, { status: 503 })));
    render(<MessageThreadPanel claimId={claimId} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i);

    mockThread(thread);
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText('Any update on my claim?')).toBeInTheDocument();
  });
});
