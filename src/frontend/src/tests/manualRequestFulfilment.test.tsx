// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ActivityCard } from '../components/activity/ActivityCard';
import type { ActivityItem } from '../components/activity/activityTypes';
import { RequestBookGroups } from '../components/activity/RequestBookGroups';
import type { RequestRecord } from '../types';

const requestRecord: RequestRecord = {
  id: 1,
  user_id: 1,
  username: 'Reader',
  book_id: 1,
  status: 'pending',
  source_hint: null,
  content_type: 'ebook',
  request_level: 'book',
  book_data: { title: 'Requested Book', author: 'Reader' },
  release_data: null,
  note: null,
  admin_note: null,
  reviewed_by: null,
  reviewed_at: null,
  created_at: '2026-02-13T10:00:00Z',
  updated_at: '2026-02-13T10:00:00Z',
};

const requestItem: ActivityItem = {
  id: 'request-1',
  kind: 'request',
  visualStatus: 'pending',
  title: 'Requested Book',
  author: 'Reader',
  metaLine: 'Book request',
  statusLabel: 'Pending',
  timestamp: 1,
  requestId: requestRecord.id,
  requestLevel: 'book',
  requestRecord,
};

describe('manual request fulfilment controls', () => {
  afterEach(cleanup);

  it('does not render Mark available in a Book request group', () => {
    render(<RequestBookGroups items={[requestItem]} onFindRelease={vi.fn()} onReject={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'Find release' })).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Mark available' })).toBeNull();
  });

  it('does not render manual approval without an attached release', () => {
    render(
      <ActivityCard item={requestItem} isAdmin isRequestDetailsOpen onRequestApprove={vi.fn()} />,
    );

    expect(screen.getByRole('button', { name: 'Browse Releases To Approve' })).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Manually Mark as Approved' })).toBeNull();
  });
});
