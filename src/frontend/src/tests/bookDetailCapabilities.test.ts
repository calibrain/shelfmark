import { describe, expect, it } from 'vitest';

import { shouldAutoFindReleases } from '../library/BookDetailPage';

describe('BookDetailPage release discovery gating', () => {
  const base = {
    canFindReleases: true,
    autoFindReleases: true,
    findRequested: false,
    hasFiles: false,
    hasInFlight: false,
    alreadyOpened: false,
  };

  it('never auto-opens release discovery without the download capability', () => {
    expect(shouldAutoFindReleases({ ...base, canFindReleases: false })).toBe(false);
  });

  it('allows an explicit find request even when automatic discovery is disabled', () => {
    expect(shouldAutoFindReleases({ ...base, autoFindReleases: false, findRequested: true })).toBe(
      true,
    );
  });

  it('does not reopen an already opened or in-flight automatic search', () => {
    expect(shouldAutoFindReleases({ ...base, alreadyOpened: true })).toBe(false);
    expect(shouldAutoFindReleases({ ...base, hasInFlight: true })).toBe(false);
  });
});
