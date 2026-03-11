import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { StatusData } from '../types/index.js';
import { wasDownloadQueuedAfterResponseError } from '../utils/downloadRecovery.js';

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

    assert.equal(wasDownloadQueuedAfterResponseError(status, 'book-1', 1_000), true);
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

    assert.equal(wasDownloadQueuedAfterResponseError(status, 'book-2', 1_006), true);
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

    assert.equal(wasDownloadQueuedAfterResponseError(status, 'book-3', 1_000), false);
  });
});
