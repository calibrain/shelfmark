import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { getActivityBadgeState } from '../utils/activityBadge.js';

describe('activityBadge.getActivityBadgeState', () => {
  it('returns null when there is no activity', () => {
    const badge = getActivityBadgeState(
      { ongoing: 0, completed: 0, errored: 0, pendingRequests: 0 },
      true
    );
    assert.equal(badge, null);
  });

  it('prioritizes red when errors are present', () => {
    const badge = getActivityBadgeState(
      { ongoing: 1, completed: 2, errored: 1, pendingRequests: 5 },
      true
    );
    assert.ok(badge);
    assert.equal(badge?.colorClass, 'bg-red-500');
    assert.equal(badge?.total, 9);
  });

  it('uses amber for admin pending requests when downloads are idle', () => {
    const badge = getActivityBadgeState(
      { ongoing: 0, completed: 0, errored: 0, pendingRequests: 3 },
      true
    );
    assert.ok(badge);
    assert.equal(badge?.colorClass, 'bg-amber-500');
    assert.equal(badge?.total, 3);
  });

  it('ignores pending requests for non-admin badge totals', () => {
    const badge = getActivityBadgeState(
      { ongoing: 0, completed: 1, errored: 0, pendingRequests: 4 },
      false
    );
    assert.ok(badge);
    assert.equal(badge?.colorClass, 'bg-green-500');
    assert.equal(badge?.total, 1);
  });
});
