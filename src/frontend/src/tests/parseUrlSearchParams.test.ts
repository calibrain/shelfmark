import { describe, it, expect } from 'vitest';

import { parseUrlSearchParams } from '../utils/parseUrlSearchParams';

describe('parseUrlSearchParams', () => {
  it('parses standard URL search filters', () => {
    const parsed = parseUrlSearchParams(
      new URLSearchParams('q=dune&author=frank+herbert&lang=en&format=epub&sort=newest'),
    );

    expect(parsed.searchInput).toBe('dune');
    expect(parsed.hasSearchParams).toBe(true);
    expect(parsed.advancedFilters).toEqual({
      author: 'frank herbert',
      lang: ['en'],
      formats: ['epub'],
      sort: 'newest',
    });
    expect(parsed.contentType).toBe(undefined);
  });

  it('parses content_type for supported values', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('q=dune&content_type=audiobook'));

    expect(parsed.searchInput).toBe('dune');
    expect(parsed.hasSearchParams).toBe(true);
    expect(parsed.contentType).toBe('audiobook');
  });

  it('ignores unsupported content_type values', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('q=dune&content_type=podcast'));

    expect(parsed.searchInput).toBe('dune');
    expect(parsed.hasSearchParams).toBe(true);
    expect(parsed.contentType).toBe(undefined);
  });

  it('keeps content_type-only links from auto-triggering a blank search', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('content_type=ebook'));

    expect(parsed.searchInput).toBe('');
    expect(parsed.hasSearchParams).toBe(false);
    expect(parsed.contentType).toBe('ebook');
  });
});
