import { describe, it, expect, vi, afterEach } from 'vitest';

import { searchBooks } from '../services/api';
import {
  describeSearchFailure,
  CLIENT_TIMEOUT_MESSAGE,
  UNREACHABLE_SOURCE_MESSAGE,
} from '../utils/searchFailureMessage';

/**
 * What a failed direct-mode search tells the user.
 *
 * Every non-auth failure used to be relabelled "Unable to reach download source. Network
 * may be restricted or mirrors blocked.", which threw away the server's explanation and
 * blamed the user's network for a protection challenge. See issue #1285.
 */

const jsonResponse = (body: unknown, status: number): Response =>
  new Response(JSON.stringify(body), {
    status,
    statusText: 'SERVICE UNAVAILABLE',
    headers: { 'Content-Type': 'application/json' },
  });

/** Drive a real searchBooks() failure so the error is the one the hook actually sees. */
const failedSearch = async (respond: () => Promise<Response>): Promise<unknown> => {
  vi.stubGlobal('fetch', vi.fn(respond));
  return searchBooks('q=dune').catch((error: unknown) => error);
};

describe('describeSearchFailure', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the sentence the server sent', async () => {
    const sentence = 'The release search ran out of time (300s).';
    const error = await failedSearch(() => Promise.resolve(jsonResponse({ error: sentence }, 503)));

    expect(describeSearchFailure(error)).toBe(sentence);
  });

  it('names the wait when the client gave up first', async () => {
    // The client's abort is the backstop for a server that never answered. It tells us
    // nothing about mirrors or the network, and the old chain reported it as if it did.
    const abort = Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' });
    const error = await failedSearch(() => Promise.reject(abort));

    expect(describeSearchFailure(error)).toBe(CLIENT_TIMEOUT_MESSAGE);
    expect(describeSearchFailure(error)).not.toBe(UNREACHABLE_SOURCE_MESSAGE);
    expect(describeSearchFailure(error)).not.toContain('mirrors');
  });

  it('falls back to the mirrors line only when nothing explained itself', async () => {
    const error = await failedSearch(() => Promise.resolve(jsonResponse({}, 503)));

    expect(describeSearchFailure(error)).toBe(UNREACHABLE_SOURCE_MESSAGE);
  });

  it('keeps a reachability message that already says the right thing', () => {
    const error = new Error('Unable to reach download source. Every mirror was quarantined.');

    expect(describeSearchFailure(error)).toBe(error.message);
  });

  it('never produces an empty sentence from a blank server message', async () => {
    // `{"message": ""}` is not the server explaining itself. Treating it as one used to
    // reach showToast('') and render an empty error toast.
    const error = await failedSearch(() => Promise.resolve(jsonResponse({ message: '' }, 503)));

    expect(describeSearchFailure(error)).toBe(UNREACHABLE_SOURCE_MESSAGE);
  });

  it('handles a non-Error rejection without inventing detail', () => {
    expect(describeSearchFailure('something odd')).toBe(UNREACHABLE_SOURCE_MESSAGE);
  });
});
