import { describe, it, expect } from 'vitest';

import { getEffectiveMetadataSort } from '../utils/metadataSort';

const hardcoverSorts = [
  { value: 'relevance', label: 'Most relevant' },
  { value: 'popularity', label: 'Most popular' },
  { value: 'series_order', label: 'Series order' },
];

describe('metadataSort', () => {
  it('keeps the current sort when it is supported', () => {
    expect(
      getEffectiveMetadataSort({
        currentSort: 'popularity',
        defaultSort: 'relevance',
        sortOptions: hardcoverSorts,
      }),
    ).toBe('popularity');
  });

  it('falls back to the provider default when the current sort is blank', () => {
    expect(
      getEffectiveMetadataSort({
        currentSort: '',
        defaultSort: 'popularity',
        sortOptions: hardcoverSorts,
      }),
    ).toBe('popularity');
  });

  it('falls back to the provider default when the current sort is unsupported', () => {
    expect(
      getEffectiveMetadataSort({
        currentSort: 'rating',
        defaultSort: 'popularity',
        sortOptions: hardcoverSorts,
      }),
    ).toBe('popularity');
  });

  it('falls back to the first supported sort when the default is unsupported', () => {
    expect(
      getEffectiveMetadataSort({
        currentSort: '',
        defaultSort: 'rating',
        sortOptions: hardcoverSorts,
      }),
    ).toBe('relevance');
  });
});
