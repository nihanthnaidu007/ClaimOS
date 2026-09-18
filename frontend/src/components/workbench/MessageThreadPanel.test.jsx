import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '@/lib/api';
import MessageThreadPanel from './MessageThreadPanel';

vi.mock('@/lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const claimId = 'CLM-MSG-1';
const THREAD_URL = `/workbench/claims/${claimId}/messages`;

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
  api.get.mockResolvedValue({ data: { claimId, messages } });
}

describe('MessageThreadPanel (adjuster case view)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockThread([]);
  });

  it('shows an empty state before any messages exist', async () => {
    render(<MessageThreadPanel claimId={claimId} />);
    expect(await screen.findByText(/no messages yet/i)).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(THREAD_URL);
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
    api.post.mockResolvedValue({ data: {} });
    mockThread([]);
    render(<MessageThreadPanel claimId={claimId} />);
    await screen.findByText(/no messages yet/i);

    await user.type(screen.getByLabelText(/message to the customer/i), 'Reply incoming');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(THREAD_URL, { body: 'Reply incoming' });
    });
    await waitFor(() => {
      expect(api.get).toHaveBeenCalledTimes(2);
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
    api.post.mockRejectedValue({ response: { status: 429 } });
    render(<MessageThreadPanel claimId={claimId} />);
    await screen.findByText(/no messages yet/i);

    await user.type(screen.getByLabelText(/message to the customer/i), 'hello');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/too quickly/i);
    expect(screen.getByLabelText(/message to the customer/i)).toHaveValue('hello');
  });

  it('surfaces a length-cap message on 422', async () => {
    const user = userEvent.setup();
    api.post.mockRejectedValue({ response: { status: 422 } });
    render(<MessageThreadPanel claimId={claimId} />);
    await screen.findByText(/no messages yet/i);

    await user.type(screen.getByLabelText(/message to the customer/i), 'x');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/between 1 and 2000/i);
  });

  it('offers a retry when the thread fails to load', async () => {
    api.get.mockRejectedValue(new Error('network down'));
    render(<MessageThreadPanel claimId={claimId} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i);

    mockThread(thread);
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText('Any update on my claim?')).toBeInTheDocument();
  });
});
