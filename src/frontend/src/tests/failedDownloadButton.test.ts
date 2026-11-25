import { describe, it, expect } from 'vitest';

import type { Book } from '../types';
import {
  addFailedRelease,
  failedDownloadButtonState,
  retryRequestId,
} from '../utils/failedDownloadButton';

const failed = (fields: Partial<Book>): Book => ({
  id: 'rel-1',
  title: 'Dungeon Crawler Carl',
  author: 'Matt Dinniman',
  ...fields,
});

describe('failedDownloadButtonState', () => {
  it('offers a failed request download again so the request can be retried with it', () => {
    expect(failedDownloadButtonState(failed({ request_id: 7, retry_available: false }))).toEqual({
      text: 'Retry',
      state: 'download',
    });
  });

  it('offers a retry when the queue can retry the download', () => {
    expect(failedDownloadButtonState(failed({ retry_available: true }))).toEqual({
      text: 'Retry',
      state: 'download',
    });
  });

  it('marks a direct download with nothing to retry from as failed', () => {
    expect(failedDownloadButtonState(failed({ retry_available: false }))).toEqual({
      text: 'Failed',
      state: 'error',
    });
  });
});

describe('addFailedRelease', () => {
  const get = { text: 'Get', state: 'download' } as const;
  const retryable = failed({ request_id: 7, retry_available: false });
  const final = failed({ id: 'rel-2', retry_available: false });

  it('keeps Retry when a release that cannot be retried is found after it', () => {
    expect(addFailedRelease(addFailedRelease(get, retryable), final)).toEqual({
      text: 'Retry',
      state: 'download',
    });
  });

  it('turns Failed into Retry when a retryable release is found after it', () => {
    expect(addFailedRelease(addFailedRelease(get, final), retryable)).toEqual({
      text: 'Retry',
      state: 'download',
    });
  });

  it('marks the book failed when no failed release can be retried', () => {
    expect(addFailedRelease(get, final)).toEqual({ text: 'Failed', state: 'error' });
  });
});

describe('retryRequestId', () => {
  it('gives the request a failed release belongs to, so Retry goes through it', () => {
    expect(retryRequestId(failed({ request_id: 7 }))).toBe(7);
  });

  it('gives nothing for a failure with no request, or no failure at all', () => {
    expect(retryRequestId(failed({ request_id: 0, retry_available: true }))).toBeUndefined();
    expect(retryRequestId(failed({}))).toBeUndefined();
    expect(retryRequestId(undefined)).toBeUndefined();
  });
});
