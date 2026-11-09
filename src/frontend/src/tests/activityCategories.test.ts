import { describe, expect, it } from 'vitest';

import {
  isAutomaticNote,
  isRetryingAutomatically,
  stripAutomaticMarker,
} from '../components/activity/activityAutomation';
import {
  getActivityCategory,
  getCategoryHint,
  getCategoryLabel,
  getVisibleCategoryOrder,
} from '../components/activity/activityCategories';
import type { ActivityItem } from '../components/activity/activityTypes';
import type { RequestRecord } from '../types';

const pendingItem = (extra: Partial<RequestRecord>): ActivityItem => ({
  id: 'request-1',
  kind: 'request',
  visualStatus: 'pending',
  title: 'T',
  author: 'A',
  metaLine: '',
  statusLabel: 'Pending',
  timestamp: 0,
  requestRecord: {
    id: 1,
    user_id: 7,
    status: 'pending',
    source_hint: null,
    content_type: 'audiobook',
    request_level: 'book',
    policy_mode: 'request_book',
    book_data: { title: 'T' },
    release_data: null,
    note: null,
    admin_note: null,
    reviewed_by: null,
    reviewed_at: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    username: 'reader',
    ...extra,
  },
});

describe('automatic notes', () => {
  it('recognises and strips the marker', () => {
    expect(isAutomaticNote('[auto] Already in the library.')).toBe(true);
    expect(isAutomaticNote('  [AUTO]   x')).toBe(true);
    expect(isAutomaticNote('a person wrote this')).toBe(false);
    expect(isAutomaticNote(null)).toBe(false);
    expect(stripAutomaticMarker('[auto] Already in the library.')).toBe('Already in the library.');
  });
});

describe('isRetryingAutomatically', () => {
  it('is true for a pending request the automation reopened', () => {
    expect(isRetryingAutomatically({ status: 'pending', admin_note: '[auto] retry batch 5' })).toBe(
      true,
    );
    expect(
      isRetryingAutomatically({
        status: 'pending',
        last_failure_reason: 'Retrying after a 20-minute cooldown (failed)',
      }),
    ).toBe(true);
    expect(
      isRetryingAutomatically({
        status: 'pending',
        last_failure_reason: 'Interrupted by a restart',
      }),
    ).toBe(true);
  });

  it('is false when nothing was reopened, or the request is not pending', () => {
    expect(isRetryingAutomatically({ status: 'pending' })).toBe(false);
    expect(
      isRetryingAutomatically({
        status: 'pending',
        last_failure_reason: 'All download sources failed',
      }),
    ).toBe(false);
    expect(isRetryingAutomatically({ status: 'rejected', admin_note: '[auto] retry' })).toBe(false);
    expect(isRetryingAutomatically(null)).toBe(false);
  });
});

describe('request categories', () => {
  it('puts a reopened request under Retrying Automatically', () => {
    expect(getActivityCategory(pendingItem({ admin_note: '[auto] retry batch 5' }))).toBe(
      'auto_retry',
    );
  });

  it('keeps a request nothing matched under No Match Yet', () => {
    expect(getActivityCategory(pendingItem({}))).toBe('needs_review');
    expect(
      getActivityCategory(pendingItem({ last_failure_reason: 'All download sources failed' })),
    ).toBe('needs_review');
  });

  it('labels the sections for what they mean', () => {
    expect(getCategoryLabel('needs_review', true)).toBe('No Match Yet');
    expect(getCategoryLabel('needs_review', false)).toBe('Waiting');
    expect(getCategoryLabel('auto_retry', true)).toBe('Retrying Automatically');
  });

  it('says what is expected of an admin, and nothing to anyone else', () => {
    expect(getCategoryHint('needs_review', true)).toContain('Searched automatically');
    expect(getCategoryHint('auto_retry', true)).toContain('Nothing to do');
    expect(getCategoryHint('needs_review', false)).toBeUndefined();
    expect(getCategoryHint('complete', true)).toBeUndefined();
  });

  it('shows the retrying section on the requests and all tabs but not under downloads', () => {
    expect(getVisibleCategoryOrder('requests')).toContain('auto_retry');
    expect(getVisibleCategoryOrder('all')).toContain('auto_retry');
    expect(getVisibleCategoryOrder('downloads')).not.toContain('auto_retry');
    expect(getVisibleCategoryOrder('history')).toEqual([]);
  });
});
