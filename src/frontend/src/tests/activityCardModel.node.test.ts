import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { ActivityItem } from '../components/activity/activityTypes.js';
import { buildActivityCardModel } from '../components/activity/activityCardModel.js';

const makeItem = (overrides: Partial<ActivityItem> = {}): ActivityItem => ({
  id: 'book-1',
  kind: 'download',
  visualStatus: 'complete',
  title: 'The Martian',
  author: 'Andy Weir',
  metaLine: 'EPUB | 1.0MB | Direct Download',
  statusLabel: 'Complete',
  timestamp: 1,
  downloadBookId: 'book-1',
  ...overrides,
});

describe('activityCardModel', () => {
  it('shows ownership in badge text for admin pending requests', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'request',
        visualStatus: 'pending',
        statusLabel: 'Pending',
        requestId: 42,
        username: 'testuser',
      }),
      true
    );

    assert.equal(model.badges.length, 1);
    assert.equal(model.badges[0]?.text, 'Needs review · testuser');
  });

  it('keeps pending label for requester-side pending requests', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'request',
        visualStatus: 'pending',
        statusLabel: 'Pending',
        requestId: 42,
      }),
      false
    );

    assert.equal(model.badges.length, 1);
    assert.equal(model.badges[0]?.text, 'Awaiting review');
  });

  it('uses requester-friendly approved wording for fulfilled requests', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'request',
        visualStatus: 'fulfilled',
        statusLabel: 'Fulfilled',
        requestId: 42,
      }),
      false
    );

    assert.equal(model.badges.length, 1);
    assert.equal(model.badges[0]?.text, 'Approved');
  });

  it('shows approved in-progress request badge while linked download is active', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'download',
        visualStatus: 'downloading',
        statusLabel: 'Downloading',
        requestId: 42,
        requestRecord: {
          id: 42,
          user_id: 7,
          status: 'fulfilled',
          source_hint: 'prowlarr',
          content_type: 'ebook',
          request_level: 'release',
          policy_mode: 'request_release',
          book_data: { title: 'The Martian', author: 'Andy Weir' },
          release_data: { source_id: 'book-1' },
          note: null,
          admin_note: null,
          reviewed_by: null,
          reviewed_at: null,
          created_at: '2026-02-13T12:00:00Z',
          updated_at: '2026-02-13T12:00:00Z',
          username: 'testuser',
        },
      }),
      false
    );

    assert.equal(model.badges.length, 2);
    assert.equal(model.badges[0]?.key, 'request');
    assert.equal(model.badges[0]?.text, 'Approved');
    assert.equal(model.badges[0]?.visualStatus, 'resolving');
  });

  it('shows request and download badges side-by-side for merged request downloads', () => {
    const model = buildActivityCardModel(
      makeItem({
        visualStatus: 'complete',
        statusLabel: 'Complete',
        statusDetail: 'Sent to Kindle',
        requestId: 42,
        requestRecord: {
          id: 42,
          user_id: 7,
          status: 'fulfilled',
          source_hint: 'prowlarr',
          content_type: 'ebook',
          request_level: 'release',
          policy_mode: 'request_release',
          book_data: { title: 'The Martian', author: 'Andy Weir' },
          release_data: { source_id: 'book-1' },
          note: null,
          admin_note: null,
          reviewed_by: null,
          reviewed_at: null,
          created_at: '2026-02-13T12:00:00Z',
          updated_at: '2026-02-13T12:00:00Z',
          username: 'testuser',
        },
      }),
      true
    );

    assert.equal(model.badges.length, 2);
    assert.equal(model.badges[0]?.key, 'request');
    assert.equal(model.badges[0]?.text, 'Approved');
    assert.equal(model.badges[1]?.key, 'download');
    assert.equal(model.badges[1]?.text, 'Sent to Kindle');
  });

  it('shows a cleared-from-queue note for fulfilled requests with cleared delivery state', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'request',
        visualStatus: 'fulfilled',
        requestId: 42,
        requestRecord: {
          id: 42,
          user_id: 7,
          status: 'fulfilled',
          delivery_state: 'cleared',
          source_hint: 'prowlarr',
          content_type: 'ebook',
          request_level: 'release',
          policy_mode: 'request_release',
          book_data: { title: 'The Martian', author: 'Andy Weir' },
          release_data: { source_id: 'book-1' },
          note: null,
          admin_note: null,
          reviewed_by: null,
          reviewed_at: null,
          created_at: '2026-02-13T12:00:00Z',
          updated_at: '2026-02-13T12:00:00Z',
          username: 'testuser',
        },
      }),
      false
    );

    assert.equal(model.noteLine, '"Cleared from queue"');
  });

  it('builds pending admin request actions from one normalized source', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'request',
        visualStatus: 'pending',
        requestId: 42,
        requestRecord: {
          id: 42,
          user_id: 7,
          status: 'pending',
          source_hint: 'prowlarr',
          content_type: 'ebook',
          request_level: 'release',
          policy_mode: 'request_release',
          book_data: { title: 'The Martian', author: 'Andy Weir' },
          release_data: { source_id: 'book-1' },
          note: null,
          admin_note: null,
          reviewed_by: null,
          reviewed_at: null,
          created_at: '2026-02-13T12:00:00Z',
          updated_at: '2026-02-13T12:00:00Z',
          username: 'testuser',
        },
      }),
      true
    );

    assert.equal(model.actions.length, 2);
    assert.equal(model.actions[0]?.kind, 'request-approve');
    assert.equal(model.actions[1]?.kind, 'request-reject');
  });

  it('attaches linked request id when dismissing merged download cards', () => {
    const model = buildActivityCardModel(
      makeItem({
        requestId: 42,
      }),
      false
    );

    assert.equal(model.actions.length, 1);
    assert.equal(model.actions[0]?.kind, 'download-dismiss');
    assert.equal(
      model.actions[0]?.kind === 'download-dismiss' ? model.actions[0].linkedRequestId : undefined,
      42
    );
  });
});
