import { describe, it, expect } from 'vitest';

import { getActivityBadgeState } from '../utils/activityBadge';

describe('activityBadge.getActivityBadgeState', () => {
  it('returns null when there is no activity', () => {
    const badge = getActivityBadgeState(
      { ongoing: 0, completed: 0, errored: 0, pendingRequests: 0 },
      true,
    );
    expect(badge).toBe(null);
  });

  it('prioritizes red when errors are present', () => {
    const badge = getActivityBadgeState(
      { ongoing: 1, completed: 2, errored: 1, pendingRequests: 5 },
      true,
    );
    expect(badge).toBeTruthy();
    expect(badge?.colorClass).toBe('bg-red-500');
    expect(badge?.total).toBe(9);
  });

  it('uses amber for admin pending requests when downloads are idle', () => {
    const badge = getActivityBadgeState(
      { ongoing: 0, completed: 0, errored: 0, pendingRequests: 3 },
      true,
    );
    expect(badge).toBeTruthy();
    expect(badge?.colorClass).toBe('bg-amber-500');
    expect(badge?.total).toBe(3);
  });

  it('ignores pending requests for non-admin badge totals', () => {
    const badge = getActivityBadgeState(
      { ongoing: 0, completed: 1, errored: 0, pendingRequests: 4 },
      false,
    );
    expect(badge).toBeTruthy();
    expect(badge?.colorClass).toBe('bg-green-500');
    expect(badge?.total).toBe(1);
  });
});
