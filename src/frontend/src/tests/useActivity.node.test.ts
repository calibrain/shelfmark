import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { getActivityErrorMessage } from '../hooks/useActivity.helpers.js';

describe('useActivity helpers', () => {
  it('returns the backend error message when present', () => {
    const error = new Error('User identity unavailable for activity workflow');

    assert.equal(
      getActivityErrorMessage(error, 'Failed to clear item'),
      'User identity unavailable for activity workflow'
    );
  });

  it('falls back to the provided message for non-error values', () => {
    assert.equal(getActivityErrorMessage(null, 'Failed to clear item'), 'Failed to clear item');
  });
});
