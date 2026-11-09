import { describe, it, expect } from 'vitest';

import { buildActivityCardModel } from '../components/activity/activityCardModel';
import type { ActivityItem } from '../components/activity/activityTypes';

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

const rejectedActionKinds = (hiddenInRejected?: boolean) =>
  buildActivityCardModel(
    makeItem({
      kind: 'request',
      visualStatus: 'rejected',
      requestId: 47,
      downloadBookId: undefined,
      hiddenInRejected,
    }),
    true,
  ).actions.map((action) => action.kind);

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
      true,
    );

    expect(model.badges.length).toBe(1);
    expect(model.badges[0]?.text).toBe('No match yet');
  });

  it('keeps pending label for requester-side pending requests', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'request',
        visualStatus: 'pending',
        statusLabel: 'Pending',
        requestId: 42,
      }),
      false,
    );

    expect(model.badges.length).toBe(1);
    expect(model.badges[0]?.text).toBe('Awaiting review');
  });

  it('uses requester-friendly approved wording for fulfilled requests', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'request',
        visualStatus: 'fulfilled',
        statusLabel: 'Fulfilled',
        requestId: 42,
      }),
      false,
    );

    expect(model.badges.length).toBe(1);
    expect(model.badges[0]?.text).toBe('Approved');
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
      false,
    );

    expect(model.badges.length).toBe(2);
    expect(model.badges[0]?.key).toBe('request');
    expect(model.badges[0]?.text).toBe('Approved');
    expect(model.badges[0]?.visualStatus).toBe('resolving');
  });

  it('shows a single download completion badge for completed merged request downloads', () => {
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
      true,
    );

    expect(model.badges.length).toBe(1);
    expect(model.badges[0]?.key).toBe('download');
    expect(model.badges[0]?.text).toBe('Sent to Kindle');
    expect(model.badges[0]?.visualStatus).toBe('complete');
  });

  it('does not render a special note for fulfilled requests with terminal delivery state', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'request',
        visualStatus: 'fulfilled',
        requestId: 42,
        requestRecord: {
          id: 42,
          user_id: 7,
          status: 'fulfilled',
          delivery_state: 'complete',
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
      false,
    );

    expect(model.noteLine).toBe(undefined);
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
      true,
    );

    expect(model.actions.length).toBe(2);
    expect(model.actions[0]?.kind).toBe('request-approve');
    expect(model.actions[1]?.kind).toBe('request-reject');
  });

  it('attaches linked request id when dismissing merged download cards', () => {
    const model = buildActivityCardModel(
      makeItem({
        requestId: 42,
      }),
      false,
    );

    expect(model.actions.length).toBe(1);
    expect(model.actions[0]?.kind).toBe('download-dismiss');
    expect(
      model.actions[0]?.kind === 'download-dismiss' ? model.actions[0].linkedRequestId : undefined,
    ).toBe(42);
  });

  it('shows retry for request-linked downloads when the backend marks them retryable', () => {
    const model = buildActivityCardModel(
      makeItem({
        visualStatus: 'error',
        statusLabel: 'Failed',
        requestId: 42,
        downloadRetryAvailable: true,
      }),
      false,
    );

    expect(model.actions.length).toBe(2);
    expect(model.actions[0]?.kind).toBe('download-retry');
    expect(model.actions[1]?.kind).toBe('download-dismiss');
  });

  it('does not show retry for error downloads without a live retry path', () => {
    const model = buildActivityCardModel(
      makeItem({
        visualStatus: 'error',
        statusLabel: 'Failed',
      }),
      false,
    );

    expect(model.actions.length).toBe(1);
    expect(model.actions[0]?.kind).toBe('download-dismiss');
  });

  it('offers reopen on a rejected request to admins only', () => {
    const rejected = makeItem({
      kind: 'request',
      visualStatus: 'rejected',
      requestId: 43,
      downloadBookId: undefined,
    });

    const adminModel = buildActivityCardModel(rejected, true);
    expect(adminModel.actions.map((action) => action.kind)).toEqual([
      'request-reopen',
      'request-dismiss',
    ]);

    const requesterModel = buildActivityCardModel(rejected, false);
    expect(requesterModel.actions.map((action) => action.kind)).toEqual(['request-dismiss']);
  });

  it('does not offer reopen on a cancelled request', () => {
    const model = buildActivityCardModel(
      makeItem({
        kind: 'request',
        visualStatus: 'cancelled',
        requestId: 44,
        downloadBookId: undefined,
      }),
      true,
    );

    expect(model.actions.map((action) => action.kind)).toEqual(['request-dismiss']);
  });

  it('tells an admin who declined a request and when', () => {
    const record = {
      id: 45,
      user_id: 7,
      status: 'rejected' as const,
      source_hint: null,
      content_type: 'ebook' as const,
      request_level: 'book' as const,
      policy_mode: 'request_book' as const,
      book_data: { title: 'My Dog Thinks It Is a Cat' },
      release_data: null,
      note: null,
      admin_note: 'already in the library',
      reviewed_by: 1,
      reviewed_at: '2026-09-22T05:31:12Z',
      created_at: '2026-09-20T03:37:29Z',
      updated_at: '2026-09-22T05:31:12Z',
      username: 'reader',
      reviewer_username: 'admin@example.com',
    };
    const item = makeItem({
      kind: 'request',
      visualStatus: 'rejected',
      requestId: 45,
      downloadBookId: undefined,
      adminNote: record.admin_note,
      requestRecord: record,
    });

    const adminLine = buildActivityCardModel(item, true).noteLine;
    expect(adminLine).toMatch(/^Declined by admin@example\.com · .+: "already in the library"$/);

    const requesterLine = buildActivityCardModel(item, false).noteLine;
    expect(requesterLine).toBe('"already in the library"');
  });

  it("says a decline the automation made was automatic, not a person's", () => {
    const record = {
      id: 46,
      user_id: 7,
      status: 'rejected' as const,
      source_hint: null,
      content_type: 'audiobook' as const,
      request_level: 'book' as const,
      policy_mode: 'request_book' as const,
      book_data: { title: 'Sauron Defeated' },
      release_data: null,
      note: null,
      admin_note: '[auto] No audiobook found on Audible (US/UK) or our sources, 2026-10-11.',
      reviewed_by: 1,
      reviewed_at: '2026-10-11T22:40:00Z',
      created_at: '2026-10-01T03:37:29Z',
      updated_at: '2026-10-11T22:40:00Z',
      username: 'reader',
      reviewer_username: 'rob@martin-up.com',
    };
    const item = makeItem({
      kind: 'request',
      visualStatus: 'rejected',
      requestId: 46,
      downloadBookId: undefined,
      adminNote: record.admin_note,
      requestRecord: record,
    });

    const adminLine = buildActivityCardModel(item, true).noteLine;
    expect(adminLine).toMatch(/^Declined automatically · .+: "No audiobook found on Audible/);
    expect(adminLine).not.toContain('rob@martin-up.com');
    expect(adminLine).not.toContain('[auto]');

    expect(buildActivityCardModel(item, false).noteLine).toBe(
      'Automatic: No audiobook found on Audible (US/UK) or our sources, 2026-10-11.',
    );
  });

  it('says an approval the automation made was automatic', () => {
    const item = makeItem({
      kind: 'request',
      visualStatus: 'fulfilled',
      requestId: 47,
      downloadBookId: undefined,
      adminNote: '[auto] Already in the library.',
      requestRecord: {
        id: 47,
        user_id: 7,
        status: 'fulfilled' as const,
        source_hint: null,
        content_type: 'ebook' as const,
        request_level: 'book' as const,
        policy_mode: 'request_book' as const,
        book_data: { title: 'X' },
        release_data: null,
        note: null,
        admin_note: '[auto] Already in the library.',
        reviewed_by: 1,
        reviewed_at: '2026-10-11T22:40:00Z',
        created_at: '2026-10-01T03:37:29Z',
        updated_at: '2026-10-11T22:40:00Z',
        username: 'reader',
      },
    });

    expect(buildActivityCardModel(item, true).noteLine).toBe('Automatic: Already in the library.');
  });

  it('says a pending request the automation reopened is retrying automatically', () => {
    const record = {
      id: 48,
      user_id: 7,
      status: 'pending' as const,
      source_hint: null,
      content_type: 'audiobook' as const,
      request_level: 'book' as const,
      policy_mode: 'request_book' as const,
      book_data: { title: 'The Big Bad Wolf' },
      release_data: null,
      note: null,
      admin_note: '[auto] retry batch 5 of 7 after the cancelled-download reopen',
      reviewed_by: null,
      reviewed_at: null,
      created_at: '2026-10-01T03:37:29Z',
      updated_at: '2026-10-16T22:40:00Z',
      username: 'reader',
      last_failure_reason: 'Peer transfer failed',
    };
    const model = buildActivityCardModel(
      makeItem({ kind: 'request', visualStatus: 'pending', requestId: 48, requestRecord: record }),
      true,
    );

    expect(model.badges[0]?.text).toBe('Retrying automatically');
    expect(model.badges[0]?.visualStatus).not.toBe('error');
  });

  it('offers hide or unhide on rejected cards only in the Rejected view', () => {
    expect(rejectedActionKinds()).toEqual(['request-reopen', 'request-dismiss']);
    expect(rejectedActionKinds(false)).toEqual([
      'request-reopen',
      'request-hide',
      'request-dismiss',
    ]);
    expect(rejectedActionKinds(true)).toEqual([
      'request-reopen',
      'request-unhide',
      'request-dismiss',
    ]);
  });
});
