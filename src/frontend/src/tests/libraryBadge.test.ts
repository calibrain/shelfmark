import { describe, expect, it } from 'vitest';

import {
  isCollectionOnly,
  isInLibrary,
  libraryBadgeContent,
} from '../components/shared/LibraryBadge';

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

describe('libraryBadgeContent', () => {
  it('is null when the book is not held', () => {
    expect(libraryBadgeContent(undefined)).toBeNull();
    expect(libraryBadgeContent({ ebook: null, audiobook: null })).toBeNull();
  });

  it('keeps the plain wording when only one library check is enabled', () => {
    expect(libraryBadgeContent({ ebook: 'owned' })).toEqual({
      text: 'In library',
      label: 'Already in your library',
    });
    expect(libraryBadgeContent({ audiobook: 'collection' })).toEqual({
      text: 'In a collection',
      label: 'In your library, inside a collection',
    });
  });

  it('names an ebook-only match when both checks are enabled', () => {
    expect(libraryBadgeContent({ ebook: 'owned', audiobook: null })).toEqual({
      text: 'Ebook',
      label: 'Ebook already in your library',
    });
  });

  it('names an audiobook-only match, shortened over cover art', () => {
    expect(libraryBadgeContent({ ebook: null, audiobook: 'owned' })).toEqual({
      text: 'Audiobook',
      label: 'Audiobook already in your library',
    });
    expect(libraryBadgeContent({ ebook: null, audiobook: 'owned' }, true)?.text).toBe('Audio');
  });

  it('names both formats when both are held', () => {
    const both = { ebook: 'owned', audiobook: 'owned' } as const;
    expect(libraryBadgeContent(both)).toEqual({
      text: 'Ebook + Audiobook',
      label: 'Ebook and audiobook already in your library',
    });
    expect(libraryBadgeContent(both, true)?.text).toBe('Ebook + Audio');
  });

  it('flags a match held only inside a collection', () => {
    expect(libraryBadgeContent({ ebook: 'collection', audiobook: null })).toEqual({
      text: 'In a collection (Ebook)',
      label: 'Ebook in your library, inside a collection',
    });
  });

  it('lists formats and explains a mix of owned and collection holdings', () => {
    expect(libraryBadgeContent({ ebook: 'owned', audiobook: 'collection' })).toEqual({
      text: 'Ebook + Audiobook',
      label: 'Ebook already in your library; audiobook in your library, inside a collection',
    });
  });
});
