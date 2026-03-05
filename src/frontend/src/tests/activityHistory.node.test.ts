import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { ActivityItem } from '../components/activity/activityTypes.js';
import { dedupeHistoryItems } from '../components/activity/activityHistory.js';

const makeRequestItem = (overrides: Partial<ActivityItem> = {}): ActivityItem => ({
  id: 'request-42',
  kind: 'request',
  visualStatus: 'fulfilled',
  title: 'Request Title',
  author: 'Request Author',
  metaLine: 'Release request',
  statusLabel: 'Approved',
  timestamp: 1,
  requestId: 42,
  requestLevel: 'release',
  requestRecord: {
    id: 42,
    user_id: 7,
    status: 'fulfilled',
    source_hint: 'prowlarr',
    content_type: 'ebook',
    request_level: 'release',
    policy_mode: 'request_release',
    book_data: { title: 'Request Title', author: 'Request Author' },
    release_data: { source: 'prowlarr', source_id: 'release-42' },
    note: null,
    admin_note: null,
    reviewed_by: null,
    reviewed_at: null,
    created_at: '2026-02-13T12:00:00Z',
    updated_at: '2026-02-13T12:00:00Z',
    username: 'alice',
  },
  ...overrides,
});

const makeDownloadItem = (overrides: Partial<ActivityItem> = {}): ActivityItem => ({
  id: 'download-42',
  kind: 'download',
  visualStatus: 'complete',
  title: 'Request Title',
  author: 'Request Author',
  metaLine: 'EPUB · 2 MB · Prowlarr',
  statusLabel: 'Complete',
  timestamp: 2,
  downloadBookId: 'download-42',
  requestId: 42,
  ...overrides,
});

describe('dedupeHistoryItems', () => {
  it('keeps release-level request history rows when they are not duplicated by a download row', () => {
    const rejectedRequest = makeRequestItem({
      id: 'request-7',
      visualStatus: 'rejected',
      statusLabel: 'Rejected',
      requestId: 7,
      requestRecord: {
        ...makeRequestItem().requestRecord!,
        id: 7,
        status: 'rejected',
        release_data: { source: 'prowlarr', source_id: 'release-7' },
      },
    });

    const items = dedupeHistoryItems([rejectedRequest]);

    assert.deepEqual(items, [rejectedRequest]);
  });

  it('drops fulfilled request history rows when a linked download row is present', () => {
    const requestItem = makeRequestItem();
    const downloadItem = makeDownloadItem();

    const items = dedupeHistoryItems([requestItem, downloadItem]);

    assert.deepEqual(items, [downloadItem]);
  });

  it('keeps fulfilled request history rows when no linked download row exists', () => {
    const requestItem = makeRequestItem();

    const items = dedupeHistoryItems([requestItem]);

    assert.deepEqual(items, [requestItem]);
  });
});
