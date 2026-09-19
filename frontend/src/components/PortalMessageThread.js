import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { resolveApiBase } from '@/lib/apiBase';

// Same-origin-safe base: resolveApiBase handles an empty OR unset
// VITE_API_BASE_URL (raw interpolation would yield "undefined/api/...").
const API = resolveApiBase();

// F5: the customer side of the claim conversation, served on the public
// portal. Access is the claim number + access code pair the customer already
// entered; every request carries both in the body (codes never ride URLs),
// and any scoping failure is the same generic "not found" the portal uses
// elsewhere. Message bodies render as React text nodes — hostile strings
// stay inert text.
const MAX_CHARS = 2000;

export default function PortalMessageThread({ claimNumber, credentials }) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [body, setBody] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    setNotFound(false);
    try {
      const res = await axios.post(
        `${API}/portal/claims/${encodeURIComponent(claimNumber)}/messages/list`,
        { accessCode: credentials.accessCode }
      );
      setMessages(res.data.messages || []);
    } catch (error) {
      if (error.response?.status === 404) {
        setNotFound(true);
      } else {
        setLoadError(true);
      }
    } finally {
      setLoading(false);
    }
  }, [claimNumber, credentials]);

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
      await axios.post(
        `${API}/portal/claims/${encodeURIComponent(claimNumber)}/messages`,
        { accessCode: credentials.accessCode, body: trimmed }
      );
      setBody('');
      await load();
    } catch (error) {
      if (error.response?.status === 429) {
        setSendError('You are sending messages too quickly. Please wait a moment.');
      } else if (error.response?.status === 404) {
        setNotFound(true);
      } else if (error.response?.status === 422) {
        setSendError(`Message must be between 1 and ${MAX_CHARS} characters.`);
      } else {
        setSendError('Could not send your message. Please try again.');
      }
    } finally {
      setSending(false);
    }
  };

  if (notFound) {
    return (
      <section aria-label="Messages" className="rounded-lg border p-4">
        <h3 className="text-sm font-semibold">Messages</h3>
        <p className="mt-2 text-sm text-muted-foreground">
          This conversation is not available for these claim details.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Messages" className="rounded-lg border p-4">
      <h3 className="mb-3 text-sm font-semibold">Messages with your adjuster</h3>

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
          No messages yet. Your adjuster will see anything you write here.
        </p>
      ) : (
        <ol className="mb-4 space-y-2" data-testid="portal-message-list">
          {messages.map((message) => (
            <li key={message.id} className="rounded border px-3 py-2 text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-xs font-medium text-muted-foreground">
                  {message.authorRole === 'customer' ? 'You' : 'Your adjuster'}
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
          aria-label="Message to your adjuster"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          maxLength={MAX_CHARS}
          rows={3}
          placeholder="Write to your adjuster…"
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
    </section>
  );
}
