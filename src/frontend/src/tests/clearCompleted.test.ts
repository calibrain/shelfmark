import { describe, it, expect } from 'vitest';

import type { ActivityItem } from '../components/activity/activityTypes';
import { getClearCompletedTargets } from '../components/activity/clearCompleted';
import type { RequestRecord } from '../types';

const makeRequestRecord = (id: number, status: RequestRecord['status']): RequestRecord => ({
  id,
  user_id: 7,
  status,
  source_hint: 'prowlarr',
  content_type: 'ebook',
  request_level: 'book',
  policy_mode: 'request_book',
  book_data: { title: 'Title', author: 'Author' },
  release_data: null,
  note: null,
  admin_note: null,
  reviewed_by: null,
  reviewed_at: null,
  created_at: '2026-02-13T12:00:00Z',
  updated_at: '2026-02-13T12:00:00Z',
  username: 'alice',
});

const makeDownloadItem = (
  downloadBookId: string,
  requestId: number | undefined,
  overrides: Partial<ActivityItem> = {},
): ActivityItem => ({
  id: `download-${downloadBookId}`,
  kind: 'download',
  visualStatus: 'complete',
  title: 'Title',
  author: 'Author',
  metaLine: 'EPUB',
  statusLabel: 'Complete',
  timestamp: 1,
  downloadBookId,
  requestId,
  ...overrides,
});

const makeRequestItem = (requestId: number, status: RequestRecord['status']): ActivityItem => ({
  id: `request-${requestId}`,
  kind: 'request',
  visualStatus: status,
  title: 'Title',
  author: 'Author',
  metaLine: 'Book request',
  statusLabel: status,
  timestamp: 1,
  requestId,
  requestRecord: makeRequestRecord(requestId, status),
});

describe('getClearCompletedTargets', () => {
  it('dismisses a finished download together with its finished request', () => {
    const targets = getClearCompletedTargets(
      [makeDownloadItem('a', 5)],
      [makeRequestItem(5, 'fulfilled')],
    );

    expect(targets).toEqual([
      { itemType: 'download', itemKey: 'download:a' },
      { itemType: 'request', itemKey: 'request:5' },
    ]);
  });

  it('dismisses the download but not a request that is still pending', () => {
    // The server refuses to dismiss a pending request, and one refusal fails the whole batch.
    const targets = getClearCompletedTargets(
      [makeDownloadItem('a', 5), makeDownloadItem('b', 6)],
      [makeRequestItem(5, 'pending'), makeRequestItem(6, 'fulfilled')],
    );

    expect(targets).toEqual([
      { itemType: 'download', itemKey: 'download:a' },
      { itemType: 'download', itemKey: 'download:b' },
      { itemType: 'request', itemKey: 'request:6' },
    ]);
  });

  it('still dismisses the request when the list has no record of it', () => {
    const targets = getClearCompletedTargets([makeDownloadItem('a', 9)], []);

    expect(targets).toEqual([
      { itemType: 'download', itemKey: 'download:a' },
      { itemType: 'request', itemKey: 'request:9' },
    ]);
  });

  it('leaves unfinished downloads alone', () => {
    const targets = getClearCompletedTargets(
      [makeDownloadItem('a', undefined, { visualStatus: 'downloading' })],
      [],
    );

    expect(targets).toEqual([]);
  });

  it('clears a finished request that never had a download', () => {
    const closed = makeRequestItem(30, 'fulfilled');
    closed.requestRecord = Object.assign({}, closed.requestRecord, {
      delivery_state: 'complete' as const,
      admin_note: '[auto] Already in the library.',
    });
    const cancelled = makeRequestItem(31, 'cancelled');

    const targets = getClearCompletedTargets([closed, cancelled], [closed, cancelled]);

    expect(targets).toEqual([
      { itemType: 'request', itemKey: 'request:30' },
      { itemType: 'request', itemKey: 'request:31' },
    ]);
  });

  it('leaves pending, rejected and still-downloading requests alone', () => {
    const pending = makeRequestItem(40, 'pending');
    const rejected = makeRequestItem(41, 'rejected');
    const downloading = makeRequestItem(42, 'fulfilled');
    downloading.requestRecord = Object.assign({}, downloading.requestRecord, {
      delivery_state: 'downloading' as const,
    });

    const items = [pending, rejected, downloading];
    expect(getClearCompletedTargets(items, items)).toEqual([]);
  });
});
