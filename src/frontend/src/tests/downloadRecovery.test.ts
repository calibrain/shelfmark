import { describe, it, expect } from 'vitest';

import type { StatusData } from '../types/index';
import { wasDownloadQueuedAfterResponseError } from '../utils/downloadRecovery';

describe('wasDownloadQueuedAfterResponseError', () => {
  it('confirms a queued download immediately from active buckets', () => {
    const status: StatusData = {
      queued: {
        'book-1': {
          id: 'book-1',
          title: 'Queued Book',
          author: 'Author',
        },
      },
    };

    expect(wasDownloadQueuedAfterResponseError(status, 'book-1', 1_000)).toBe(true);
  });

  it('confirms a recent terminal item for the same request window', () => {
    const status: StatusData = {
      complete: {
        'book-2': {
          id: 'book-2',
          title: 'Completed Book',
          author: 'Author',
          added_time: 1_005,
        },
      },
    };

    expect(wasDownloadQueuedAfterResponseError(status, 'book-2', 1_006)).toBe(true);
  });

  it('ignores stale terminal entries from older attempts', () => {
    const status: StatusData = {
      complete: {
        'book-3': {
          id: 'book-3',
          title: 'Old Completed Book',
          author: 'Author',
          added_time: 900,
        },
      },
    };

    expect(wasDownloadQueuedAfterResponseError(status, 'book-3', 1_000)).toBe(false);
  });
});
