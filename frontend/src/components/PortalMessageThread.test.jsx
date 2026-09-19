import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';

import PortalMessageThread from './PortalMessageThread';
import { server } from '../test/handlers';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;
const claimNumber = 'CLM-MSG-1';
const credentials = { claimNumber, accessCode: 'secret-code-123' };

const thread = [
  {
    id: 'msg_1',
    claimId: claimNumber,
    authorRole: 'adjuster',
    authorId: 'usr_adjuster',
    body: 'Could you confirm the incident date?',
    createdAt: '2026-09-18T09:00:00+00:00',
    readAt: null,
  },
  {
    id: 'msg_2',
    claimId: claimNumber,
    authorRole: 'customer',
    authorId: 'portal-customer',
    body: 'It was September 14th in the evening.',
    createdAt: '2026-09-18T09:10:00+00:00',
    readAt: null,
  },
];

const listUrl = `${API_BASE}/portal/claims/${claimNumber}/messages/list`;
const sendUrl = `${API_BASE}/portal/claims/${claimNumber}/messages`;

function mockThread(messages) {
  server.use(
    http.post(listUrl, () => HttpResponse.json({ claimId: claimNumber, messages }))
  );
}

describe('PortalMessageThread (customer portal)', () => {
  afterEach(() => server.resetHandlers());

  it('sends the access code in the body of every request', async () => {
    const seen = { list: null, send: null };
    server.use(
      http.post(listUrl, async ({ request }) => {
        seen.list = await request.json();
        return HttpResponse.json({ claimId: claimNumber, messages: [] });
      }),
      http.post(sendUrl, async ({ request }) => {
        seen.send = await request.json();
        return HttpResponse.json({}, { status: 201 });
      })
    );

    const user = userEvent.setup();
    render(<PortalMessageThread claimNumber={claimNumber} credentials={credentials} />);
    await screen.findByText(/no messages yet/i);

    await user.type(screen.getByLabelText(/message to your adjuster/i), 'Thanks for the update');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    await waitFor(() => {
      expect(seen.send).toEqual({
        accessCode: 'secret-code-123',
        body: 'Thanks for the update',
      });
    });
    expect(seen.list).toEqual({ accessCode: 'secret-code-123' });
  });

  it('renders the thread chronologically with customer-facing role labels', async () => {
    mockThread(thread);
    render(<PortalMessageThread claimNumber={claimNumber} credentials={credentials} />);

    expect(await screen.findByText('Could you confirm the incident date?')).toBeInTheDocument();
    expect(screen.getByText('It was September 14th in the evening.')).toBeInTheDocument();
    expect(screen.getAllByText('You')).toHaveLength(1);
    expect(screen.getAllByText('Your adjuster')).toHaveLength(1);
  });

  it('renders hostile strings as inert text, never markup', async () => {
    mockThread([
      {
        ...thread[0],
        id: 'msg_hostile',
        body: '<script>alert("xss")</script><img src=x onerror=alert(1)><iframe src="https://evil.example"></iframe>',
      },
    ]);
    const { container } = render(
      <PortalMessageThread claimNumber={claimNumber} credentials={credentials} />
    );

    expect(await screen.findByText(/<script>alert\("xss"\)<\/script>/)).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('shows the generic not-available state on a scoped 404', async () => {
    server.use(http.post(listUrl, () => new HttpResponse(null, { status: 404 })));
    render(<PortalMessageThread claimNumber={claimNumber} credentials={credentials} />);
    expect(await screen.findByText(/not available for these claim details/i)).toBeInTheDocument();
  });

  it('surfaces a rate-limit message on 429 and keeps the draft', async () => {
    const user = userEvent.setup();
    mockThread([]);
    server.use(http.post(sendUrl, () => new HttpResponse(null, { status: 429 })));
    render(<PortalMessageThread claimNumber={claimNumber} credentials={credentials} />);
    await screen.findByText(/no messages yet/i);

    await user.type(screen.getByLabelText(/message to your adjuster/i), 'hello');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/too quickly/i);
    expect(screen.getByLabelText(/message to your adjuster/i)).toHaveValue('hello');
  });

  it('offers a retry when the thread fails to load', async () => {
    mockThread([]);
    server.use(http.post(listUrl, () => new HttpResponse(null, { status: 503 })));
    render(<PortalMessageThread claimNumber={claimNumber} credentials={credentials} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i);

    mockThread(thread);
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText('Could you confirm the incident date?')).toBeInTheDocument();
  });

  it('sends follow-up messages and refetches the thread after each send', async () => {
    const user = userEvent.setup();
    let stored = [];
    server.use(
      http.post(listUrl, () => HttpResponse.json({ claimId: claimNumber, messages: stored })),
      http.post(sendUrl, async ({ request }) => {
        const body = await request.json();
        stored = [
          ...stored,
          {
            id: `msg_${stored.length + 1}`,
            claimId: claimNumber,
            authorRole: 'customer',
            authorId: 'portal-customer',
            body: body.body,
            createdAt: '2026-09-18T09:20:00+00:00',
            readAt: null,
          },
        ];
        return HttpResponse.json({}, { status: 201 });
      })
    );

    render(<PortalMessageThread claimNumber={claimNumber} credentials={credentials} />);
    await screen.findByText(/no messages yet/i);

    await user.type(screen.getByLabelText(/message to your adjuster/i), 'First message');
    await user.click(screen.getByRole('button', { name: /send message/i }));
    await screen.findByText('First message');

    await user.type(screen.getByLabelText(/message to your adjuster/i), 'Second message');
    await user.click(screen.getByRole('button', { name: /send message/i }));
    await screen.findByText('Second message');
    expect(screen.getAllByText('First message')).toHaveLength(1);
  });
});
