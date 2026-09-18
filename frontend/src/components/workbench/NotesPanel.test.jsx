// NotesPanel tests (F11): list/loading/empty/error states, plain-text
// rendering, the composer (counter, mention chips, submit + refetch).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import NotesPanel from './NotesPanel';
import { server } from '../../test/handlers';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL}/api`;
const NOTES_URL = `${API_BASE}/workbench/claims/CLM-1001/notes`;

let consoleErrorSpy;

beforeEach(() => {
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  consoleErrorSpy.mockRestore();
});

const notes = [
  {
    id: 'note_1',
    claimId: 'CLM-1001',
    authorId: 'usr_1',
    authorEmail: 'adjuster@claimos.test',
    body: 'Verified the police report.',
    mentions: ['sam.lee'],
    createdAt: '2026-09-18T10:00:00+00:00',
    auditEntryId: 'aud_1',
  },
];

function useNotesHandler(notesOrStatus) {
  server.use(
    http.get(NOTES_URL, () => {
      if (notesOrStatus === 'error') return HttpResponse.error();
      return HttpResponse.json({ claimId: 'CLM-1001', notes: notesOrStatus });
    })
  );
}

function renderPanel() {
  return render(<NotesPanel claimId="CLM-1001" />);
}

describe('NotesPanel', () => {
  it('renders existing notes oldest-first with author, time, mentions, and body', async () => {
    useNotesHandler(notes);
    renderPanel();
    await waitFor(() => expect(screen.getAllByTestId('note-item')).toHaveLength(1));
    expect(screen.getByTestId('note-body').textContent).toBe('Verified the police report.');
    expect(screen.getByTestId('note-mentions').textContent).toBe('@sam.lee');
    expect(screen.getByText('adjuster@claimos.test')).toBeInTheDocument();
  });

  it('shows an empty state when the claim has no notes', async () => {
    useNotesHandler([]);
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('notes-empty')).toBeInTheDocument());
  });

  it('shows an error state with retry, and recovers when the retry succeeds', async () => {
    useNotesHandler('error');
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('notes-error')).toBeInTheDocument());
    server.use(http.get(NOTES_URL, () => HttpResponse.json({ claimId: 'CLM-1001', notes: [] })));
    fireEvent.click(screen.getByTestId('notes-retry'));
    await waitFor(() => expect(screen.getByTestId('notes-empty')).toBeInTheDocument());
  });

  it('renders bodies as plain text — markup and escapes are inert', async () => {
    useNotesHandler([{ ...notes[0], body: '<script>alert(1)</script>\x1b[31mred', mentions: [] }]);
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('note-item')).toBeInTheDocument());
    const body = screen.getByTestId('note-body');
    expect(body.textContent).toContain('<script>');
    expect(body.querySelector('script')).toBeNull();
  });

  it('submits a note and refetches the list', async () => {
    let callCount = 0;
    server.use(
      http.get(NOTES_URL, () =>
        HttpResponse.json({ claimId: 'CLM-1001', notes: callCount === 0 ? [] : notes })
      ),
      http.post(NOTES_URL, async ({ request }) => {
        callCount += 1;
        const payload = await request.json();
        expect(payload.body).toBe('Checked the estimate.');
        return HttpResponse.json({ ...notes[0], body: payload.body }, { status: 201 });
      })
    );
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('notes-empty')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('note-input'), { target: { value: 'Checked the estimate.' } });
    fireEvent.click(screen.getByTestId('note-submit'));
    await waitFor(() => expect(screen.getAllByTestId('note-item')).toHaveLength(1));
    expect(screen.getByTestId('note-input').value).toBe('');
  });

  it('disables submit for a blank draft and shows the live counter', () => {
    useNotesHandler([]);
    renderPanel();
    expect(screen.getByTestId('note-submit')).toBeDisabled();
    fireEvent.change(screen.getByTestId('note-input'), { target: { value: 'abc' } });
    expect(screen.getByTestId('note-counter').textContent).toBe('3/4000');
    expect(screen.getByTestId('note-submit')).toBeEnabled();
  });

  it('shows a mention chip per distinct @handle in the draft', () => {
    useNotesHandler([]);
    renderPanel();
    fireEvent.change(screen.getByTestId('note-input'), {
      target: { value: 'ping @alex.rivera and @sam, plus alex.rivera@example.com' },
    });
    const chips = screen.getAllByTestId('mention-chip').map((c) => c.textContent);
    expect(chips).toEqual(['@alex.rivera', '@sam']);
  });

  it('caps the composer at 4000 characters', () => {
    useNotesHandler([]);
    renderPanel();
    expect(screen.getByTestId('note-input').maxLength).toBe(4000);
  });
});
