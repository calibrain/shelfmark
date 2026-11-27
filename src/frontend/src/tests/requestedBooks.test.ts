import { describe, expect, it } from 'vitest';

import type { Book, RequestRecord } from '../types';
import {
  NO_PENDING_REQUESTS,
  bookRequestKeys,
  buildPendingRequestKeys,
  requestedFormats,
} from '../utils/requestedBooks';

const book = (overrides: Partial<Book> = {}): Book => ({
  id: 'bk1',
  title: 'The Housemaid',
  author: 'Freida McFadden',
  provider: 'hardcover',
  provider_id: '12345',
  ...overrides,
});

const request = (overrides: Partial<RequestRecord> = {}): RequestRecord => ({
  id: 1,
  user_id: 7,
  status: 'pending',
  source_hint: null,
  content_type: 'ebook',
  request_level: 'book',
  policy_mode: 'request_book',
  book_data: { provider: 'hardcover', provider_id: '12345' },
  release_data: null,
  note: null,
  admin_note: null,
  reviewed_by: null,
  reviewed_at: null,
  created_at: '2026-10-08T00:00:00Z',
  updated_at: '2026-10-08T00:00:00Z',
  ...overrides,
});

describe('buildPendingRequestKeys', () => {
  it('keys a pending request by provider and provider id, under its content type', () => {
    const pending = buildPendingRequestKeys([request()]);

    expect(pending.ebook.has('hardcover:12345')).toBe(true);
    expect(pending.audiobook.size).toBe(0);
  });

  it.each(['fulfilled', 'rejected', 'cancelled'] as const)('leaves out a %s request', (status) => {
    // A fulfilled request already shows as a download, and a rejected or
    // cancelled one is exactly the case where asking again is right.
    const pending = buildPendingRequestKeys([request({ status })]);

    expect(pending.ebook.size + pending.audiobook.size).toBe(0);
  });

  it('keeps ebook and audiobook requests apart', () => {
    const pending = buildPendingRequestKeys([request({ content_type: 'audiobook' })]);

    expect(pending.audiobook.has('hardcover:12345')).toBe(true);
    expect(pending.ebook.size).toBe(0);
  });

  it('ignores a request with no book data or no provider id', () => {
    const pending = buildPendingRequestKeys([
      request({ book_data: null }),
      request({ id: 2, book_data: { provider: 'hardcover' } }),
    ]);

    expect(pending.ebook.size).toBe(0);
  });

  it('normalises the provider case and a numeric id', () => {
    const pending = buildPendingRequestKeys([
      request({ book_data: { provider: 'Hardcover', provider_id: 12345 } }),
    ]);

    expect(pending.ebook.has('hardcover:12345')).toBe(true);
  });
});

describe('bookRequestKeys', () => {
  it('keys a metadata result by provider and provider id', () => {
    expect(bookRequestKeys(book())).toEqual(['hardcover:12345']);
  });

  it('falls back to the book id when there is no provider id', () => {
    expect(bookRequestKeys(book({ provider_id: undefined }))).toEqual(['hardcover:bk1']);
  });

  it('offers the metadata fallback a request would have stored', () => {
    // buildMetadataBookRequestData writes `provider: book.provider || 'metadata'`.
    expect(bookRequestKeys(book({ provider: undefined }))).toEqual(['metadata:12345']);
  });

  it('keys a direct result by its source, the way the direct payload stores it', () => {
    // buildDirectRequestPayload writes `provider: book.source || book.provider`
    // and `provider_id: book.provider_id || book.id`.
    const direct = book({ provider: undefined, provider_id: undefined, source: 'prowlarr' });

    expect(bookRequestKeys(direct)).toContain('prowlarr:bk1');
  });

  it('yields nothing for a result with no identity', () => {
    expect(bookRequestKeys(book({ id: '', provider_id: '' }))).toEqual([]);
  });
});

describe('requestedFormats', () => {
  it('names the format of a pending request for the same result', () => {
    expect(requestedFormats(book(), buildPendingRequestKeys([request()]))).toEqual(['ebook']);
  });

  it('lists both formats in display order', () => {
    const pending = buildPendingRequestKeys([
      request({ id: 2, content_type: 'audiobook' }),
      request({ id: 1 }),
    ]);

    expect(requestedFormats(book(), pending)).toEqual(['ebook', 'audiobook']);
  });

  it('does not match a different book from the same provider', () => {
    const other = book({ provider_id: '99999' });

    expect(requestedFormats(other, buildPendingRequestKeys([request()]))).toEqual([]);
  });

  it('does not match the same id under a different provider', () => {
    // Provider ids are only unique within a provider.
    const other = book({ provider: 'openlibrary' });

    expect(requestedFormats(other, buildPendingRequestKeys([request()]))).toEqual([]);
  });

  it('is empty when nothing is pending', () => {
    expect(requestedFormats(book(), NO_PENDING_REQUESTS)).toEqual([]);
  });

  it('matches a Hardcover sync request against the Hardcover result', () => {
    // hardcover_sync stores provider 'hardcover' and the Hardcover id as a string.
    const pending = buildPendingRequestKeys([
      request({
        content_type: 'audiobook',
        book_data: { provider: 'hardcover', provider_id: '12345', content_type: 'audiobook' },
      }),
    ]);

    expect(requestedFormats(book(), pending)).toEqual(['audiobook']);
  });
});
