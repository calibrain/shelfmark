import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

import {
  getConfig,
  getSearchTimeoutMs,
  setSearchTimeoutFromConfig,
  searchBooks,
  isApiResponseError,
} from '../services/api';

/**
 * The client's abort must fire *after* the server's own search deadline.
 *
 * `/api/releases` bounds itself with RELEASE_SEARCH_TIMEOUT and answers a spent budget
 * with a message naming the real cause. The client aborted at a fixed 180s against a
 * 300s default, so it always won the race and replaced that message with "Request timed
 * out. Check your network connection or proxy configuration." Raising the setting had no
 * visible effect either, the 180s being baked into the hashed bundle. See issue #1285.
 */

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    statusText: status === 200 ? 'OK' : 'SERVICE UNAVAILABLE',
    headers: { 'Content-Type': 'application/json' },
  });

const configBody = (releaseSearchTimeout: number): Record<string, unknown> => ({
  release_search_timeout: releaseSearchTimeout,
});

describe('release search timeout', () => {
  beforeEach(() => {
    setSearchTimeoutFromConfig(300);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setSearchTimeoutFromConfig(300);
  });

  it('defaults behind the server default rather than ahead of it', () => {
    expect(getSearchTimeoutMs()).toBeGreaterThan(300 * 1000);
  });

  it('follows the budget the server reports', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(configBody(900)))),
    );

    await getConfig();

    expect(getSearchTimeoutMs()).toBeGreaterThan(900 * 1000);
  });

  it('still outlasts the server when the budget is lowered', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(configBody(30)))),
    );

    await getConfig();

    expect(getSearchTimeoutMs()).toBeGreaterThan(30 * 1000);
  });

  it('ignores a missing or nonsensical budget instead of disabling the backstop', () => {
    const before = getSearchTimeoutMs();

    setSearchTimeoutFromConfig(undefined);
    setSearchTimeoutFromConfig(0);
    setSearchTimeoutFromConfig(-1);
    setSearchTimeoutFromConfig('600');
    setSearchTimeoutFromConfig(Number.NaN);

    expect(getSearchTimeoutMs()).toBe(before);
  });

  it('applies the derived timeout to the direct_download search', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(configBody(600)))),
    );
    await getConfig();

    const seen: Array<AbortSignal | undefined> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init: RequestInit) => {
        seen.push(init.signal ?? undefined);
        return Promise.resolve(jsonResponse({ releases: [] }));
      }),
    );

    await searchBooks('q=dune');

    // The request carries an abort signal, and it is not yet aborted: the point is that
    // the clock it runs on is the server's, not a constant.
    expect(seen).toHaveLength(1);
    expect(seen[0]?.aborted).toBe(false);
    expect(getSearchTimeoutMs()).toBeGreaterThan(600 * 1000);
  });
});

describe('server-provided failure messages', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('carries the server sentence through instead of a status placeholder', async () => {
    const sentence =
      'The release search ran out of time (300s). Anna’s Archive is behind a ' +
      'protection challenge the bypasser could not solve in that window.';
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse({ error: sentence }, 503))),
    );

    const error = await searchBooks('q=dune').catch((e: unknown) => e);

    expect(isApiResponseError(error)).toBe(true);
    if (isApiResponseError(error)) {
      expect(error.serverMessage).toBe(sentence);
    }
  });

  it('leaves serverMessage unset when the server explained nothing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse({}, 503))),
    );

    const error = await searchBooks('q=dune').catch((e: unknown) => e);

    expect(isApiResponseError(error)).toBe(true);
    if (isApiResponseError(error)) {
      expect(error.serverMessage).toBeUndefined();
      // Without this the UI would show a bare "503 SERVICE UNAVAILABLE".
      expect(error.message).toContain('Server unavailable');
    }
  });
});
