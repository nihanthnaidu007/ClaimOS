import { useCallback, useEffect, useRef, useState } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

// F5: adjuster <-> customer conversation for one claim. Bodies are plain
// text — the server strips control characters and enforces the cap; the
// client mirrors the cap and renders every message as a React text node
// (never dangerouslySetInnerHTML), so hostile strings display inert.
const MAX_CHARS = 2000;

export default function MessageThreadPanel({ claimId }) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [body, setBody] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');
  const listEndRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const res = await api.get(`/workbench/claims/${encodeURIComponent(claimId)}/messages`);
      setMessages(res.data.messages || []);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [claimId]);

  useEffect(() => {
    load();
  }, [load]);

  const send = async (event) => {
    event.preventDefault();
    const trimmed = body.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setSendError('');
    try {
      await api.post(`/workbench/claims/${encodeURIComponent(claimId)}/messages`, { body: trimmed });
      setBody('');
      await load();
    } catch (error) {
      if (error.response?.status === 429) {
        setSendError('You are sending messages too quickly. Wait a moment and try again.');
      } else if (error.response?.status === 422) {
        setSendError(`Message must be between 1 and ${MAX_CHARS} characters.`);
      } else {
        setSendError('Could not send the message. Try again.');
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <section aria-label="Messages" className="rounded-lg border bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold">Messages with the customer</h3>

      {loading ? (
        <p role="status" className="py-4 text-sm text-muted-foreground">
          Loading messages…
        </p>
      ) : loadError ? (
        <div role="alert" className="py-4 text-sm">
          <p>Could not load the conversation.</p>
          <Button type="button" variant="outline" size="sm" className="mt-2" onClick={load}>
            Retry
          </Button>
        </div>
      ) : messages.length === 0 ? (
        <p className="py-4 text-sm text-muted-foreground">
          No messages yet. Write to the customer below.
        </p>
      ) : (
        <ol className="mb-4 space-y-2" data-testid="message-list">
          {messages.map((message) => (
            <li
              key={message.id}
              className="rounded border px-3 py-2 text-sm"
              data-testid={`message-${message.authorRole}`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-xs font-medium text-muted-foreground">
                  {message.authorRole === 'customer' ? 'Customer' : 'Adjuster'}
                </span>
                <time className="text-xs text-muted-foreground">
                  {new Date(message.createdAt).toLocaleString()}
                </time>
              </div>
              {/* Plain text by construction: React escapes text-node content. */}
              <p className="mt-1 whitespace-pre-wrap break-words">{message.body}</p>
            </li>
          ))}
        </ol>
      )}

      <form onSubmit={send} className="space-y-2">
        <Textarea
          aria-label="Message to the customer"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          maxLength={MAX_CHARS}
          rows={3}
          placeholder="Write to the customer…"
        />
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">
            {body.length}/{MAX_CHARS}
          </span>
          <Button type="submit" size="sm" disabled={sending || !body.trim()}>
            {sending ? 'Sending…' : 'Send message'}
          </Button>
        </div>
        {sendError ? (
          <p role="alert" className="text-sm text-destructive">
            {sendError}
          </p>
        ) : null}
      </form>
      <div ref={listEndRef} />
    </section>
  );
}
