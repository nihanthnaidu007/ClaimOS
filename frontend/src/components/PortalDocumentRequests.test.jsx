// PortalDocumentRequests component test (spec F4) — the customer-side
// checklist: requested/received/waived states, client-side type and size
// prechecks, the upload call shape (credentials + requestId + file on the
// wire), and the per-request server-error mapping.
//
// The multipart POST is asserted at the axios boundary (vi.spyOn) rather than
// through MSW: MSW's XHR interceptor cannot serialize a jsdom FormData body in
// Node ("reading '_buffer'"), so a handler never sees the request. The real
// multipart round trip is covered by the upload-portal Playwright spec against
// the live fixture backend.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import axios from 'axios';

import PortalDocumentRequests from './PortalDocumentRequests';

const CREDENTIALS = { claimNumber: 'CLM-20260918-010', accessCode: 'ABCD-1234' };

const requested = {
  id: 'dreq_1',
  title: 'Repair estimate',
  description: 'A signed estimate from the shop.',
  status: 'requested',
};
const received = {
  ...requested,
  id: 'dreq_2',
  title: 'Police report',
  description: 'The report filed at the scene.',
  status: 'received',
};
const waived = {
  ...requested,
  id: 'dreq_3',
  title: 'Old photos',
  description: 'Photos taken before the repair.',
  status: 'waived',
};

const renderCard = (props = {}) =>
  render(
    <PortalDocumentRequests
      credentials={CREDENTIALS}
      requests={[requested]}
      onUploaded={vi.fn()}
      {...props}
    />
  );

const chooseFile = (input, file) => fireEvent.change(input, { target: { files: [file] } });

const UPLOAD_URL = 'http://localhost:8001/api/status/upload-document';

afterEach(() => vi.restoreAllMocks());

describe('PortalDocumentRequests', () => {
  it('lists every request with its lifecycle chip', () => {
    renderCard({ requests: [requested, received, waived] });

    const items = screen.getAllByTestId('docreq-item');
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveAttribute('data-status', 'requested');
    expect(items[1]).toHaveAttribute('data-status', 'received');
    expect(items[2]).toHaveAttribute('data-status', 'waived');
    expect(screen.getByText('Repair estimate')).toBeInTheDocument();
    expect(screen.getByText('A signed estimate from the shop.')).toBeInTheDocument();
  });

  it('is hidden entirely when the claim has no document requests', () => {
    const { container } = renderCard({ requests: [] });
    expect(container).toBeEmptyDOMElement();
  });

  it('is hidden without credentials', () => {
    const { container } = renderCard({ credentials: null });
    expect(container).toBeEmptyDOMElement();
  });

  it('uploads with the credential pair and request id, then refreshes', async () => {
    const onUploaded = vi.fn();
    const postSpy = vi
      .spyOn(axios, 'post')
      .mockResolvedValue({ status: 201, data: { requestId: 'dreq_1', status: 'received' } });
    renderCard({ onUploaded });

    chooseFile(
      screen.getByTestId('docreq-upload-input'),
      new File(['%PDF'], 'estimate.pdf', { type: 'application/pdf' })
    );
    fireEvent.click(screen.getByTestId('docreq-upload-submit'));

    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
    expect(postSpy).toHaveBeenCalledTimes(1);
    const [url, form] = postSpy.mock.calls[0];
    expect(url).toBe(UPLOAD_URL);
    expect(form.get('claimNumber')).toBe('CLM-20260918-010');
    expect(form.get('accessCode')).toBe('ABCD-1234');
    expect(form.get('requestId')).toBe('dreq_1');
    expect(form.get('file')).toMatchObject({ name: 'estimate.pdf', type: 'application/pdf' });
    expect(screen.queryByTestId('docreq-upload-error')).not.toBeInTheDocument();
  });

  it('rejects a disallowed type client-side without a network call', async () => {
    const onUploaded = vi.fn();
    const postSpy = vi.spyOn(axios, 'post');
    renderCard({ onUploaded });

    chooseFile(
      screen.getByTestId('docreq-upload-input'),
      new File(['plain'], 'notes.txt', { type: 'text/plain' })
    );
    fireEvent.click(screen.getByTestId('docreq-upload-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('docreq-upload-error')).toHaveTextContent(/isn't accepted/i)
    );
    expect(postSpy).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('rejects an oversized file client-side without a network call', async () => {
    const onUploaded = vi.fn();
    const postSpy = vi.spyOn(axios, 'post');
    renderCard({ onUploaded });

    const big = new File(['x'], 'big.pdf', { type: 'application/pdf' });
    Object.defineProperty(big, 'size', { value: 10 * 1024 * 1024 + 1 });
    chooseFile(screen.getByTestId('docreq-upload-input'), big);
    fireEvent.click(screen.getByTestId('docreq-upload-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('docreq-upload-error')).toHaveTextContent(/10 MB limit/i)
    );
    expect(postSpy).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it.each([
    [413, /10 MB limit/i],
    [415, /isn't accepted/i],
    [422, /empty/i],
    [409, /refresh the page to see its current state/i],
    [429, /too many attempts/i],
    [500, /couldn't match that request/i],
  ])('maps a %i server rejection to its customer-facing message', async (status, pattern) => {
    const onUploaded = vi.fn();
    vi.spyOn(axios, 'post').mockRejectedValue({ response: { status, data: { detail: 'x' } } });
    renderCard({ onUploaded });

    chooseFile(
      screen.getByTestId('docreq-upload-input'),
      new File(['%PDF'], 'estimate.pdf', { type: 'application/pdf' })
    );
    fireEvent.click(screen.getByTestId('docreq-upload-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('docreq-upload-error')).toHaveTextContent(pattern)
    );
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('maps a network failure to the refresh-and-retry message', async () => {
    vi.spyOn(axios, 'post').mockRejectedValue(new Error('Network Error'));
    renderCard();

    chooseFile(
      screen.getByTestId('docreq-upload-input'),
      new File(['%PDF'], 'estimate.pdf', { type: 'application/pdf' })
    );
    fireEvent.click(screen.getByTestId('docreq-upload-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('docreq-upload-error')).toHaveTextContent(/couldn't match that request/i)
    );
  });

  it('keeps the submit button disabled until a file is chosen', () => {
    renderCard();

    expect(screen.getByTestId('docreq-upload-submit')).toBeDisabled();
    chooseFile(
      screen.getByTestId('docreq-upload-input'),
      new File(['%PDF'], 'estimate.pdf', { type: 'application/pdf' })
    );
    expect(screen.getByTestId('docreq-upload-submit')).toBeEnabled();
  });
});
