import type { Book, ContentType, RequestRecord } from '../types';
import { toContentType } from './requestPayload';

/** Keys of every book with a request still awaiting a decision, per format. */
export type PendingRequestKeys = Readonly<Record<ContentType, ReadonlySet<string>>>;

const NO_KEYS: ReadonlySet<string> = new Set();

export const NO_PENDING_REQUESTS: PendingRequestKeys = { ebook: NO_KEYS, audiobook: NO_KEYS };

const FORMATS: readonly ContentType[] = ['ebook', 'audiobook'];

/**
 * Namespaced identity of a book as a request stores it.
 *
 * Provider ids are only unique within a provider, so the provider is part of
 * the key rather than decoration.
 */
const requestKey = (provider: unknown, providerId: unknown): string => {
  const source = typeof provider === 'string' ? provider.trim().toLowerCase() : '';
  let id = '';
  if (typeof providerId === 'string') {
    id = providerId.trim();
  } else if (typeof providerId === 'number') {
    id = String(providerId);
  }
  return source && id ? `${source}:${id}` : '';
};

/**
 * Every identity a search result could have been requested under.
 *
 * Exact rather than fuzzy: a pending request is about the entry someone
 * clicked, not about owning the work in some edition. Two keys can come back
 * because the request payload builders name the provider differently. The
 * metadata one stores `book.provider || 'metadata'`, the direct one stores the
 * browse source (`book.source || book.provider`), and a result does not say
 * which path created an earlier request.
 *
 * `getBrowseSource` is not used because it throws on a book with neither
 * field. This runs for every row of every result set, so a malformed row has
 * to read as "not requested".
 */
export const bookRequestKeys = (book: Book): string[] => {
  const id = (book.provider_id || book.id || '').trim();
  if (!id) return [];

  const keys = new Set<string>();
  for (const provider of [book.provider || 'metadata', book.source || book.provider]) {
    const key = requestKey(provider, id);
    if (key) keys.add(key);
  }
  return [...keys];
};

/**
 * Keys of every book with a request still awaiting a decision, per format.
 *
 * Only `pending` counts. A fulfilled request has become a download the button
 * already reports on, and a rejected or cancelled one is exactly the case
 * where asking again is the right thing to do. Ebook and audiobook are kept
 * apart so the pill can name the format, the way the library pills do.
 */
export const buildPendingRequestKeys = (records: readonly RequestRecord[]): PendingRequestKeys => {
  const keys: Record<ContentType, Set<string>> = { ebook: new Set(), audiobook: new Set() };

  for (const record of records) {
    if (record.status !== 'pending' || !record.book_data) continue;
    const key = requestKey(record.book_data.provider, record.book_data.provider_id);
    if (key) keys[toContentType(record.content_type)].add(key);
  }

  return keys;
};

/** Formats this result has a pending request for, in display order. */
export const requestedFormats = (book: Book, pending: PendingRequestKeys): ContentType[] => {
  if (pending.ebook.size === 0 && pending.audiobook.size === 0) return [];

  const keys = bookRequestKeys(book);
  return FORMATS.filter((format) => keys.some((key) => pending[format].has(key)));
};
