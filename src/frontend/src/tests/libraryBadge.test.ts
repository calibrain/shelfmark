import { describe, expect, it } from 'vitest';

import { isInLibrary } from '../components/shared/LibraryBadge';

describe('isInLibrary', () => {
  it('is false without a library result', () => {
    expect(isInLibrary(undefined)).toBe(false);
    expect(isInLibrary(null)).toBe(false);
    expect(isInLibrary({})).toBe(false);
  });

  it('is false when no format is owned', () => {
    expect(isInLibrary({ ebook: false })).toBe(false);
    expect(isInLibrary({ ebook: false, audiobook: false })).toBe(false);
  });

  it('is true when any format is owned', () => {
    expect(isInLibrary({ ebook: true })).toBe(true);
    expect(isInLibrary({ ebook: false, audiobook: true })).toBe(true);
  });
});
