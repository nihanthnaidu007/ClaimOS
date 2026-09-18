// Document checklists panel tests (spec F3): the four async states, the
// validated create form, the inline edit, waive, and stale-value recovery.
// Mutations are asserted through the bodies the panel actually sends.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { http, HttpResponse, delay } from 'msw';
import DocumentRequests from './DocumentRequests';
import { server } from '../../test/handlers';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;

let consoleErrorSpy;

beforeEach(() => {
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  consoleErrorSpy.mockRestore();
});

function row(overrides = {}) {
  return {
    id: 'dreq_1',
    claim_id: 'CLM-1001',
    title: 'Repair estimate',
    description: 'Itemized and signed by the shop.',
    status: 'requested',
    requested_by: 'usr_1',
    document_id: null,
    created_at: '2026-09-17T10:00:00+00:00',
    updated_at: '2026-09-17T10:00:00+00:00',
    ...overrides,
  };
}

// Checklist server with stateful data: GET serves the store (mutations apply
// to it, so a post-mutation refresh sees the new state), POST/PATCH are
// recorded for body assertions.
function useChecklistHandlers({ rows = [] } = {}) {
  const store = rows.map((entry) => ({ ...entry }));
  const created = [];
  const patches = [];
  let postCount = 0;
  server.use(
    http.get(`${API_BASE}/claims/CLM-1001/document-requests`, () =>
      HttpResponse.json([...store, ...created])
    ),
    http.post(`${API_BASE}/claims/CLM-1001/document-requests`, async ({ request }) => {
      postCount += 1;
      const body = await request.json();
      const createdRow = row({ id: `dreq_new_${postCount}`, ...body });
      created.push(createdRow);
      return HttpResponse.json(createdRow, { status: 201 });
    }),
    http.patch(
      `${API_BASE}/claims/CLM-1001/document-requests/:requestId`,
      async ({ request, params }) => {
        const body = await request.json();
        patches.push({ requestId: params.requestId, body });
        const target = [...store, ...created].find(
          (entry) => entry.id === params.requestId
        );
        if (body.waive) target.status = 'waived';
        if (body.title) target.title = body.title;
        if (body.description !== undefined) target.description = body.description;
        return HttpResponse.json(target);
      }
    )
  );
  return { created, patches, postCount: () => postCount };
}

describe('DocumentRequests', () => {
  it('shows skeleton rows while loading, then the checklist with status chips', async () => {
    server.use(
      http.get(`${API_BASE}/claims/CLM-1001/document-requests`, async () => {
        await delay(100);
        return HttpResponse.json([
          row(),
          row({ id: 'dreq_2', title: 'Photos of damage', description: '', status: 'received' }),
          row({ id: 'dreq_3', title: 'Police report', status: 'waived', description: '' }),
        ]);
      })
    );

    render(<DocumentRequests claimId="CLM-1001" />);
    expect(screen.getByTestId('docreq-skeleton')).toBeInTheDocument();
    const items = await screen.findAllByTestId('docreq-item');
    expect(items).toHaveLength(3);
    expect(items[0].dataset.status).toBe('requested');
    expect(screen.getAllByTestId('docreq-status')[1]).toHaveTextContent('Received');
    expect(screen.getAllByTestId('docreq-status')[2]).toHaveTextContent('Waived');
    // Only requested items are actionable.
    expect(items[0]).toHaveTextContent('Waive request');
    expect(items[1]).not.toHaveTextContent('Waive request');
  });

  it('renders the instructive empty state when no documents are requested', async () => {
    useChecklistHandlers({ rows: [] });
    render(<DocumentRequests claimId="CLM-1001" />);
    expect(await screen.findByTestId('docreq-empty')).toHaveTextContent(
      'No documents requested yet'
    );
  });

  it('names the load failure and recovers via Retry', async () => {
    let failing = true;
    server.use(
      http.get(`${API_BASE}/claims/CLM-1001/document-requests`, () =>
        failing
          ? new HttpResponse(null, { status: 500 })
          : HttpResponse.json([row()])
      )
    );
    render(<DocumentRequests claimId="CLM-1001" />);
    expect(await screen.findByTestId('docreq-error')).toHaveTextContent(
      'Couldn’t load the document checklist'
    );

    failing = false;
    fireEvent.click(screen.getByTestId('docreq-retry'));
    expect(await screen.findAllByTestId('docreq-item')).toHaveLength(1);
    expect(screen.queryByTestId('docreq-error')).not.toBeInTheDocument();
  });

  it('creates a request with a trimmed title, clears the form, and refreshes the list', async () => {
    const handlers = useChecklistHandlers({ rows: [] });
    render(<DocumentRequests claimId="CLM-1001" />);
    await screen.findByTestId('docreq-empty');

    fireEvent.change(screen.getByTestId('docreq-title-input'), {
      target: { value: '  Towing receipts  ' },
    });
    fireEvent.click(screen.getByTestId('docreq-submit'));

    await waitFor(() => expect(handlers.postCount()).toBe(1));
    expect(screen.getByTestId('docreq-title-input').value).toBe('');
    const items = await screen.findAllByTestId('docreq-item');
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent('Towing receipts');
    expect(screen.getAllByTestId('docreq-status')[0]).toHaveTextContent('Requested');
  });

  it('blocks an empty-title submit with an inline error, focus, and no POST', async () => {
    const handlers = useChecklistHandlers({ rows: [] });
    render(<DocumentRequests claimId="CLM-1001" />);
    await screen.findByTestId('docreq-empty');

    fireEvent.click(screen.getByTestId('docreq-submit'));
    expect(await screen.findByTestId('docreq-title-error')).toBeInTheDocument();
    expect(screen.getByTestId('docreq-title-input')).toHaveFocus();
    expect(handlers.postCount()).toBe(0);
  });

  it('rejects a title over the 120-character backend limit before any POST', async () => {
    const handlers = useChecklistHandlers({ rows: [] });
    render(<DocumentRequests claimId="CLM-1001" />);
    await screen.findByTestId('docreq-empty');

    fireEvent.change(screen.getByTestId('docreq-title-input'), {
      target: { value: 'x'.repeat(121) },
    });
    fireEvent.click(screen.getByTestId('docreq-submit'));
    expect(await screen.findByTestId('docreq-title-error')).toHaveTextContent(
      'Keep the title to 120 characters or fewer'
    );
    expect(handlers.postCount()).toBe(0);
  });

  it('rejects a description over the 2000-character backend limit', async () => {
    const handlers = useChecklistHandlers({ rows: [] });
    render(<DocumentRequests claimId="CLM-1001" />);
    await screen.findByTestId('docreq-empty');

    fireEvent.change(screen.getByTestId('docreq-title-input'), {
      target: { value: 'Proof of loss' },
    });
    fireEvent.change(screen.getByTestId('docreq-description-input'), {
      target: { value: 'y'.repeat(2001) },
    });
    fireEvent.click(screen.getByTestId('docreq-submit'));
    expect(await screen.findByTestId('docreq-description-error')).toHaveTextContent(
      'Keep the description to 2000 characters or fewer'
    );
    expect(handlers.postCount()).toBe(0);
  });

  it('keeps entries and shows a banner when the server rejects a valid create', async () => {
    server.use(
      http.get(`${API_BASE}/claims/CLM-1001/document-requests`, () => HttpResponse.json([])),
      http.post(`${API_BASE}/claims/CLM-1001/document-requests`, () =>
        new HttpResponse(null, { status: 500 })
      )
    );
    render(<DocumentRequests claimId="CLM-1001" />);
    await screen.findByTestId('docreq-empty');

    fireEvent.change(screen.getByTestId('docreq-title-input'), {
      target: { value: 'Repair estimate' },
    });
    fireEvent.click(screen.getByTestId('docreq-submit'));
    expect(await screen.findByTestId('docreq-create-error')).toHaveTextContent(
      'Your entries are safe'
    );
    expect(screen.getByTestId('docreq-title-input').value).toBe('Repair estimate');
  });

  it('edits a requested item inline and refreshes', async () => {
    const handlers = useChecklistHandlers({ rows: [row()] });
    render(<DocumentRequests claimId="CLM-1001" />);
    const items = await screen.findAllByTestId('docreq-item');

    fireEvent.click(within(items[0]).getByTestId('docreq-edit'));
    const editInput = screen.getByTestId('docreq-edit-title');
    expect(editInput.value).toBe('Repair estimate');
    fireEvent.change(editInput, { target: { value: 'Itemized repair estimate' } });
    fireEvent.click(screen.getByTestId('docreq-save'));

    await waitFor(() => expect(handlers.patches).toHaveLength(1));
    expect(handlers.patches[0]).toEqual({
      requestId: 'dreq_1',
      body: { title: 'Itemized repair estimate', description: 'Itemized and signed by the shop.' },
    });
    expect(await screen.findByTestId('docreq-list')).toHaveTextContent(
      'Itemized repair estimate'
    );
  });

  it('waives a requested item and shows the Waived chip', async () => {
    const handlers = useChecklistHandlers({ rows: [row()] });
    render(<DocumentRequests claimId="CLM-1001" />);
    const items = await screen.findAllByTestId('docreq-item');

    fireEvent.click(within(items[0]).getByTestId('docreq-waive'));
    await waitFor(() => expect(handlers.patches).toHaveLength(1));
    expect(handlers.patches[0].body).toEqual({ waive: true });
    expect(await screen.findByTestId('docreq-list')).toHaveTextContent('Waived');
  });

  it('surfaces a 409 conflict on waive as an item-level error', async () => {
    server.use(
      http.get(`${API_BASE}/claims/CLM-1001/document-requests`, () => HttpResponse.json([row()])),
      http.patch(`${API_BASE}/claims/CLM-1001/document-requests/dreq_1`, () =>
        HttpResponse.json({ detail: 'Only requested documents can be waived.' }, { status: 409 })
      )
    );
    render(<DocumentRequests claimId="CLM-1001" />);
    const items = await screen.findAllByTestId('docreq-item');

    fireEvent.click(within(items[0]).getByTestId('docreq-waive'));
    expect(await screen.findByTestId('docreq-action-error')).toHaveTextContent(
      'Only requested documents can be waived.'
    );
  });
});
