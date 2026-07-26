import { describe, expect, it } from 'vitest';

import type { ActivityItem } from '../components/activity/activityTypes';
import { groupPendingRequestsByBook } from '../components/activity/RequestBookGroups';

const requestItem = (id: number, bookId: number, username: string): ActivityItem => ({
  id: `request-${id}`,
  kind: 'request',
  visualStatus: 'pending',
  title: 'Fallback title',
  author: '',
  metaLine: '',
  statusLabel: 'Pending',
  timestamp: id,
  requestId: id,
  requestRecord: {
    id,
    user_id: id,
    status: 'pending',
    book_id: bookId,
    book_title: 'The Canonical Book',
    book_author: 'Canonical Author',
    book_cover_url: 'https://example.com/cover.jpg',
    source_hint: null,
    content_type: 'ebook',
    request_level: 'book',
    book_data: null,
    release_data: null,
    note: null,
    admin_note: null,
    reviewed_by: null,
    reviewed_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    username,
  },
});

describe('groupPendingRequestsByBook', () => {
  it('groups pending requests using the canonical flat book fields', () => {
    const fulfilled = requestItem(3, 18, 'carol');
    const groups = groupPendingRequestsByBook([
      requestItem(1, 17, 'alice'),
      requestItem(2, 17, 'bob'),
      {
        ...fulfilled,
        visualStatus: 'fulfilled',
        requestRecord: fulfilled.requestRecord && {
          ...fulfilled.requestRecord,
          status: 'fulfilled',
        },
      },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0]?.book).toMatchObject({ id: '17', title: 'The Canonical Book' });
    expect(groups[0]?.requests.map((item) => item.requestRecord?.username)).toEqual([
      'alice',
      'bob',
    ]);
  });
});
