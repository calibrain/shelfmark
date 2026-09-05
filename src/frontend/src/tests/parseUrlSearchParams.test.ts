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

  it('parses content_type=combined as a combined-mode override', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('q=dune&content_type=combined'));

    expect(parsed.searchInput).toBe('dune');
    expect(parsed.hasSearchParams).toBe(true);
    expect(parsed.contentType).toBe(undefined);
    expect(parsed.combinedMode).toBe(true);
  });

  it('keeps combined-only links from auto-triggering a blank search', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('content_type=combined'));

    expect(parsed.searchInput).toBe('');
    expect(parsed.hasSearchParams).toBe(false);
    expect(parsed.contentType).toBe(undefined);
    expect(parsed.combinedMode).toBe(true);
  });

  it('parses search_by as the Search By target', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('search_by=manual&q=dune'));

    expect(parsed.searchBy).toBe('manual');
    expect(parsed.searchInput).toBe('dune');
    expect(parsed.hasSearchParams).toBe(true);
  });

  it('trims search_by but keeps its casing for case-sensitive provider field keys', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('search_by=+MANUAL+'));

    expect(parsed.searchBy).toBe('MANUAL');
  });

  it('keeps search_by-only links from auto-triggering a blank search', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('search_by=manual'));

    expect(parsed.searchBy).toBe('manual');
    expect(parsed.searchInput).toBe('');
    expect(parsed.hasSearchParams).toBe(false);
  });

  it('leaves search_by undefined when absent', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('q=dune'));

    expect(parsed.searchBy).toBe(undefined);
  });
});
