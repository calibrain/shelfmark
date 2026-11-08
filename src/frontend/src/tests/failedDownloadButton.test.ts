import { describe, it, expect } from 'vitest';

import type { Book } from '../types';
import { failedDownloadButtonState } from '../utils/failedDownloadButton';

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
