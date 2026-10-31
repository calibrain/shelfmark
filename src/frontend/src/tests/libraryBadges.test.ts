import { describe, it, expect } from 'vitest';

import { isCollectionHolding, ownedFormats } from '../components/shared/LibraryBadges';

describe('LibraryBadges.ownedFormats', () => {
  it('returns nothing when the library check is off or nothing is owned', () => {
    expect(ownedFormats(undefined)).toEqual([]);
    expect(ownedFormats(null)).toEqual([]);
    expect(ownedFormats({})).toEqual([]);
    expect(ownedFormats({ ebook: null, audiobook: null })).toEqual([]);
  });

  it('lists owned formats in display order, ignoring unknown keys', () => {
    expect(ownedFormats({ audiobook: 'owned', ebook: 'owned' })).toEqual(['ebook', 'audiobook']);
    expect(ownedFormats({ audiobook: 'owned' })).toEqual(['audiobook']);
    expect(ownedFormats({ ebook: 'owned', audiobook: undefined })).toEqual(['ebook']);
  });
});

describe('collection holdings', () => {
  it('counts a collection as held, and names it as one', () => {
    expect(ownedFormats({ ebook: 'collection' })).toEqual(['ebook']);
    expect(isCollectionHolding({ ebook: 'collection' }, 'ebook')).toBe(true);
    expect(isCollectionHolding({ ebook: 'owned' }, 'ebook')).toBe(false);
    expect(isCollectionHolding({ ebook: null }, 'ebook')).toBe(false);
    expect(isCollectionHolding(undefined, 'audiobook')).toBe(false);
  });
});
