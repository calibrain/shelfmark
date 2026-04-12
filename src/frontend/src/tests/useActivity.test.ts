import { describe, it, expect } from 'vitest';

import { getActivityErrorMessage } from '../hooks/useActivity.helpers';

describe('useActivity helpers', () => {
  it('returns the backend error message when present', () => {
    const error = new Error('User identity unavailable for activity workflow');

    expect(getActivityErrorMessage(error, 'Failed to clear item')).toBe(
      'User identity unavailable for activity workflow',
    );
  });

  it('falls back to the provided message for non-error values', () => {
    expect(getActivityErrorMessage(null, 'Failed to clear item')).toBe('Failed to clear item');
  });
});
