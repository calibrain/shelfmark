import { describe, expect, it } from 'vitest';

import { buildUrlSearchHash } from '../utils/urlSearchHash';

describe('buildUrlSearchHash', () => {
  it('builds a hash reflecting query, search_by and a non-default content_type', () => {
    const hash = buildUrlSearchHash({
      searchInput: 'dune',
      searchBy: 'manual',
      contentType: 'audiobook',
      combinedMode: false,
      advancedFilters: {},
    });

    expect(new URLSearchParams(hash).get('q')).toBe('dune');
    expect(new URLSearchParams(hash).get('search_by')).toBe('manual');
    expect(new URLSearchParams(hash).get('content_type')).toBe('audiobook');
  });

  it('omits content_type when it is the ebook default', () => {
    const hash = buildUrlSearchHash({
      searchInput: 'dune',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });

    expect(new URLSearchParams(hash).has('content_type')).toBe(false);
  });

  it('omits search_by when it is the general default', () => {
    const hash = buildUrlSearchHash({
      searchInput: 'dune',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });

    expect(new URLSearchParams(hash).has('search_by')).toBe(false);
  });

  it('sets content_type=combined when combinedMode is true', () => {
    const hash = buildUrlSearchHash({
      searchInput: 'dune',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: true,
      advancedFilters: {},
    });

    expect(new URLSearchParams(hash).get('content_type')).toBe('combined');
  });

  it('mirrors advanced filters (isbn/author/title/sort/content/lang/format)', () => {
    const hash = buildUrlSearchHash({
      searchInput: '',
      searchBy: 'author',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {
        author: 'frank herbert',
        lang: ['en', 'de'],
        formats: ['epub'],
        sort: 'newest',
      },
    });

    const params = new URLSearchParams(hash);
    expect(params.get('author')).toBe('frank herbert');
    expect(params.get('sort')).toBe('newest');
    expect(params.getAll('lang')).toEqual(['en', 'de']);
    expect(params.getAll('format')).toEqual(['epub']);
  });

  it('produces an empty string when there is nothing to reflect', () => {
    const hash = buildUrlSearchHash({
      searchInput: '',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });

    expect(hash).toBe('');
  });
});
