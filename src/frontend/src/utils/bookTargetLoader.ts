import { fetchBookTargetOptionsBatch, type BookTargetOption } from '../services/api';
import { onBookTargetChange } from './bookTargetEvents';
import type { Book } from '../types';

// Providers that support the book targets feature.
// Centralised here so the check isn't hardcoded across every view component.
const PROVIDERS_WITH_TARGETS = new Set(['hardcover']);

/** Returns true if the book's provider supports the book-target dropdown. */
export const bookSupportsTargets = (book: Book): boolean =>
  Boolean(book.provider && book.provider_id && PROVIDERS_WITH_TARGETS.has(book.provider));

type PendingRequest = {
  bookId: string;
  resolve: (options: BookTargetOption[]) => void;
  reject: (error: unknown) => void;
};

const BATCH_SIZE = 50;
const CACHE_TTL_MS = 5 * 60 * 1000;

const pendingByProvider = new Map<string, PendingRequest[]>();
let flushScheduled = false;

// Client-side cache to avoid re-fetching on view mode switches and re-renders
const cache = new Map<string, { options: BookTargetOption[]; expiresAt: number }>();

const cacheKey = (provider: string, bookId: string) => `${provider}:${bookId}`;

const getCached = (provider: string, bookId: string): BookTargetOption[] | undefined => {
  const entry = cache.get(cacheKey(provider, bookId));
  if (!entry) return undefined;
  if (Date.now() > entry.expiresAt) {
    cache.delete(cacheKey(provider, bookId));
    return undefined;
  }
  return entry.options;
};

const setCache = (provider: string, bookId: string, options: BookTargetOption[]) => {
  cache.set(cacheKey(provider, bookId), { options, expiresAt: Date.now() + CACHE_TTL_MS });
};

export const invalidateCache = (provider: string, bookId: string) => {
  cache.delete(cacheKey(provider, bookId));
};

// Invalidate cache when a target changes
onBookTargetChange((event) => {
  invalidateCache(event.provider, event.bookId);
});

const fetchChunked = async (
  provider: string,
  bookIds: string[],
): Promise<Map<string, BookTargetOption[]>> => {
  if (bookIds.length <= BATCH_SIZE) {
    return fetchBookTargetOptionsBatch(provider, bookIds);
  }

  const merged = new Map<string, BookTargetOption[]>();
  const chunks: string[][] = [];
  for (let i = 0; i < bookIds.length; i += BATCH_SIZE) {
    chunks.push(bookIds.slice(i, i + BATCH_SIZE));
  }

  const results = await Promise.all(
    chunks.map((chunk) => fetchBookTargetOptionsBatch(provider, chunk)),
  );
  for (const chunkResult of results) {
    for (const [bookId, options] of chunkResult) {
      merged.set(bookId, options);
    }
  }
  return merged;
};

const flush = async () => {
  flushScheduled = false;
  const snapshot = new Map(pendingByProvider);
  pendingByProvider.clear();

  await Promise.all(
    [...snapshot].map(async ([provider, requests]) => {
      // Deduplicate and separate cached vs uncached
      const uncachedIds = new Set<string>();
      for (const req of requests) {
        if (!getCached(provider, req.bookId)) {
          uncachedIds.add(req.bookId);
        }
      }

      try {
        let fetched = new Map<string, BookTargetOption[]>();
        if (uncachedIds.size > 0) {
          fetched = await fetchChunked(provider, [...uncachedIds]);
          for (const [bookId, options] of fetched) {
            setCache(provider, bookId, options);
          }
        }

        for (const req of requests) {
          const options = getCached(provider, req.bookId) ?? fetched.get(req.bookId) ?? [];
          req.resolve(options);
        }
      } catch (error) {
        for (const req of requests) {
          req.reject(error);
        }
      }
    }),
  );
};

/**
 * Load book target options using automatic batching.
 *
 * Multiple calls within the same microtask are coalesced into a single
 * batch API request per provider. Results are cached client-side to avoid
 * re-fetching on view mode switches and re-renders.
 */
export const loadBookTargets = (
  provider: string,
  bookId: string,
): Promise<BookTargetOption[]> => {
  const cached = getCached(provider, bookId);
  if (cached) {
    return Promise.resolve(cached);
  }

  return new Promise<BookTargetOption[]>((resolve, reject) => {
    let list = pendingByProvider.get(provider);
    if (!list) {
      list = [];
      pendingByProvider.set(provider, list);
    }
    list.push({ bookId, resolve, reject });

    if (!flushScheduled) {
      flushScheduled = true;
      // Use queueMicrotask so all synchronous mounts in the same tick are batched
      queueMicrotask(() => void flush());
    }
  });
};
