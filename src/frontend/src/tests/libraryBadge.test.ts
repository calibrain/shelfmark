import { describe, expect, it } from 'vitest';

import { isCollectionOnly, isInLibrary } from '../components/shared/LibraryBadge';

describe('isInLibrary', () => {
  it('is false without a library result', () => {
    expect(isInLibrary(undefined)).toBe(false);
    expect(isInLibrary(null)).toBe(false);
    expect(isInLibrary({})).toBe(false);
  });

  it('is false when no format holds it', () => {
    expect(isInLibrary({ ebook: null })).toBe(false);
    expect(isInLibrary({ ebook: null, audiobook: null })).toBe(false);
  });

  it('is true for a book held outright or inside a collection', () => {
    expect(isInLibrary({ ebook: 'owned' })).toBe(true);
    expect(isInLibrary({ ebook: 'collection' })).toBe(true);
    expect(isInLibrary({ ebook: null, audiobook: 'owned' })).toBe(true);
  });
});

describe('isCollectionOnly', () => {
  it('is true only when every holding is inside a collection', () => {
    expect(isCollectionOnly({ ebook: 'collection' })).toBe(true);
    expect(isCollectionOnly({ ebook: 'collection', audiobook: null })).toBe(true);
  });

  it('is false when any format holds the book on its own', () => {
    expect(isCollectionOnly({ ebook: 'owned' })).toBe(false);
    expect(isCollectionOnly({ ebook: 'collection', audiobook: 'owned' })).toBe(false);
  });

  it('is false when the book is not held at all', () => {
    expect(isCollectionOnly({ ebook: null })).toBe(false);
    expect(isCollectionOnly(undefined)).toBe(false);
  });
});
