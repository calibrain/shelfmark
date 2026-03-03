import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { parseUrlSearchParams } from '../utils/parseUrlSearchParams.js';

describe('parseUrlSearchParams', () => {
  it('parses standard URL search filters', () => {
    const parsed = parseUrlSearchParams(
      new URLSearchParams('q=dune&author=frank+herbert&lang=en&format=epub&sort=newest')
    );

    assert.equal(parsed.searchInput, 'dune');
    assert.equal(parsed.hasSearchParams, true);
    assert.deepEqual(parsed.advancedFilters, {
      author: 'frank herbert',
      lang: ['en'],
      formats: ['epub'],
      sort: 'newest',
    });
    assert.equal(parsed.contentType, undefined);
  });

  it('parses content_type for supported values', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('q=dune&content_type=audiobook'));

    assert.equal(parsed.searchInput, 'dune');
    assert.equal(parsed.hasSearchParams, true);
    assert.equal(parsed.contentType, 'audiobook');
  });

  it('ignores unsupported content_type values', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('q=dune&content_type=podcast'));

    assert.equal(parsed.searchInput, 'dune');
    assert.equal(parsed.hasSearchParams, true);
    assert.equal(parsed.contentType, undefined);
  });

  it('keeps content_type-only links from auto-triggering a blank search', () => {
    const parsed = parseUrlSearchParams(new URLSearchParams('content_type=ebook'));

    assert.equal(parsed.searchInput, '');
    assert.equal(parsed.hasSearchParams, false);
    assert.equal(parsed.contentType, 'ebook');
  });
});
