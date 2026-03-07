import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { getEffectiveMetadataSort } from '../utils/metadataSort.js';

const hardcoverSorts = [
  { value: 'relevance', label: 'Most relevant' },
  { value: 'popularity', label: 'Most popular' },
  { value: 'series_order', label: 'Series order' },
];

describe('metadataSort', () => {
  it('keeps the current sort when it is supported', () => {
    assert.equal(
      getEffectiveMetadataSort({
        currentSort: 'popularity',
        defaultSort: 'relevance',
        sortOptions: hardcoverSorts,
      }),
      'popularity',
    );
  });

  it('falls back to the provider default when the current sort is blank', () => {
    assert.equal(
      getEffectiveMetadataSort({
        currentSort: '',
        defaultSort: 'popularity',
        sortOptions: hardcoverSorts,
      }),
      'popularity',
    );
  });

  it('falls back to the provider default when the current sort is unsupported', () => {
    assert.equal(
      getEffectiveMetadataSort({
        currentSort: 'rating',
        defaultSort: 'popularity',
        sortOptions: hardcoverSorts,
      }),
      'popularity',
    );
  });

  it('falls back to the first supported sort when the default is unsupported', () => {
    assert.equal(
      getEffectiveMetadataSort({
        currentSort: '',
        defaultSort: 'rating',
        sortOptions: hardcoverSorts,
      }),
      'relevance',
    );
  });
});
