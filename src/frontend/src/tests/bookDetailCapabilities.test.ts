import { describe, expect, it } from 'vitest';

import { bookMembershipLabel, shouldAutoFindReleases } from '../library/BookDetailPage';

describe('BookDetailPage release discovery gating', () => {
  const base = {
    canFindReleases: true,
    autoFindReleases: true,
    firstAddIntent: true,
    hasFiles: false,
    hasInFlight: false,
    alreadyOpened: false,
  };

  it('never auto-opens release discovery without the download capability', () => {
    expect(shouldAutoFindReleases({ ...base, canFindReleases: false })).toBe(false);
  });

  it('requires the transient first-add intent', () => {
    expect(shouldAutoFindReleases({ ...base, firstAddIntent: false })).toBe(false);
    expect(shouldAutoFindReleases({ ...base, autoFindReleases: false })).toBe(false);
  });

  it('does not reopen an already opened or in-flight automatic search', () => {
    expect(shouldAutoFindReleases({ ...base, alreadyOpened: true })).toBe(false);
    expect(shouldAutoFindReleases({ ...base, hasInFlight: true })).toBe(false);
  });
});

describe('BookDetailPage membership label', () => {
  it('does not claim an administrator is a member of another library', () => {
    expect(bookMembershipLabel(false)).toBe('Not in your library');
    expect(bookMembershipLabel(true)).toBe('In your library');
  });
});
