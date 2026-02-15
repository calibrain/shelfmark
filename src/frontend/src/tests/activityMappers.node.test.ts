import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Book, RequestRecord } from '../types/index.js';
import { downloadToActivityItem, requestToActivityItem } from '../components/activity/activityMappers.js';

const makeBook = (overrides: Partial<Book> = {}): Book => ({
  id: 'book-1',
  title: 'The Test Book',
  author: 'Test Author',
  ...overrides,
});

const makeRequest = (overrides: Partial<RequestRecord> = {}): RequestRecord => ({
  id: 42,
  user_id: 7,
  status: 'pending',
  source_hint: 'prowlarr',
  content_type: 'ebook',
  request_level: 'release',
  policy_mode: 'request_release',
  book_data: {
    title: 'Request Title',
    author: 'Request Author',
    preview: 'https://example.com/cover.jpg',
  },
  release_data: {
    source: 'prowlarr',
    format: 'epub',
    size: '2 MB',
  },
  note: 'please add this',
  admin_note: null,
  reviewed_by: null,
  reviewed_at: null,
  created_at: '2026-02-13T12:00:00Z',
  updated_at: '2026-02-13T12:00:00Z',
  username: 'alice',
  ...overrides,
});

describe('activityMappers.downloadToActivityItem', () => {
  it('maps every download status key to its visual status', () => {
    const statusExpectations: Array<{
      statusKey: 'queued' | 'resolving' | 'locating' | 'downloading' | 'complete' | 'error' | 'cancelled';
      expectedVisualStatus: string;
    }> = [
      { statusKey: 'queued', expectedVisualStatus: 'queued' },
      { statusKey: 'resolving', expectedVisualStatus: 'resolving' },
      { statusKey: 'locating', expectedVisualStatus: 'locating' },
      { statusKey: 'downloading', expectedVisualStatus: 'downloading' },
      { statusKey: 'complete', expectedVisualStatus: 'complete' },
      { statusKey: 'error', expectedVisualStatus: 'error' },
      { statusKey: 'cancelled', expectedVisualStatus: 'cancelled' },
    ];

    statusExpectations.forEach(({ statusKey, expectedVisualStatus }) => {
      const item = downloadToActivityItem(makeBook(), statusKey);
      assert.equal(item.visualStatus, expectedVisualStatus);
    });
  });

  it('maps download items with meta line and status fields', () => {
    const item = downloadToActivityItem(
      makeBook({
        format: 'epub',
        size: '3 MB',
        source_display_name: 'Direct Download',
        username: 'alice',
        added_time: 123,
      }),
      'queued'
    );

    assert.equal(item.kind, 'download');
    assert.equal(item.visualStatus, 'queued');
    assert.equal(item.statusLabel, 'Queued');
    assert.equal(item.metaLine, 'EPUB · 3 MB · Direct Download · alice');
    assert.equal(item.progress, 5);
    assert.equal(item.progressAnimated, true);
    assert.equal(item.timestamp, 123);
  });

  it('maps downloading progress using 20 + progress*0.8', () => {
    const item = downloadToActivityItem(makeBook({ progress: 60 }), 'downloading');
    assert.equal(item.visualStatus, 'downloading');
    assert.equal(item.progress, 68);
  });

  it('falls back to normalized source name when source_display_name is missing', () => {
    const item = downloadToActivityItem(makeBook({ source: 'direct_download' }), 'complete');
    assert.equal(item.metaLine, 'Direct Download');
  });

  it('omits empty meta parts cleanly', () => {
    const item = downloadToActivityItem(makeBook({ format: 'epub', size: undefined }), 'error');
    assert.equal(item.metaLine, 'EPUB');
  });
});

describe('activityMappers.requestToActivityItem', () => {
  it('maps request statuses to visual statuses', () => {
    const statuses: Array<{ input: RequestRecord['status']; expected: string }> = [
      { input: 'pending', expected: 'pending' },
      { input: 'fulfilled', expected: 'fulfilled' },
      { input: 'rejected', expected: 'rejected' },
      { input: 'cancelled', expected: 'cancelled' },
    ];

    statuses.forEach(({ input, expected }) => {
      const item = requestToActivityItem(makeRequest({ status: input }), 'user');
      assert.equal(item.visualStatus, expected);
    });
  });

  it('maps release-level admin request with release meta and username', () => {
    const item = requestToActivityItem(makeRequest(), 'admin');

    assert.equal(item.kind, 'request');
    assert.equal(item.visualStatus, 'pending');
    assert.equal(item.metaLine, 'EPUB · 2 MB · Prowlarr · alice');
    assert.equal(item.requestId, 42);
    assert.equal(item.requestLevel, 'release');
    assert.equal(item.requestNote, 'please add this');
    assert.equal(item.statusLabel, 'Pending');
    assert.ok(item.timestamp > 0);
  });

  it('maps book-level user request without username in meta line', () => {
    const item = requestToActivityItem(
      makeRequest({
        request_level: 'book',
        release_data: null,
        source_hint: '*',
      }),
      'user'
    );

    assert.equal(item.metaLine, 'Book request');
  });

  it('maps audiobook book-level request with audiobook label', () => {
    const item = requestToActivityItem(
      makeRequest({
        request_level: 'book',
        content_type: 'audiobook',
        release_data: null,
        source_hint: '*',
      }),
      'admin'
    );

    assert.equal(item.metaLine, 'Audiobook request · alice');
  });

  it('maps rejected requests with admin note', () => {
    const item = requestToActivityItem(
      makeRequest({
        status: 'rejected',
        admin_note: 'Not available',
      }),
      'user'
    );

    assert.equal(item.visualStatus, 'rejected');
    assert.equal(item.adminNote, 'Not available');
  });

  it('does not append username to meta line for user viewer role', () => {
    const item = requestToActivityItem(makeRequest(), 'user');
    assert.equal(item.metaLine, 'EPUB · 2 MB · Prowlarr');
  });
});
