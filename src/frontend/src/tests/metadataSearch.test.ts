import { afterEach, describe, expect, it, vi } from 'vitest';

import { searchMetadata } from '../services/api';

describe('metadata search', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('does not request the provider for whitespace-only search input', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);

    await expect(searchMetadata('   ', 40, 'relevance', { author: '  ' })).resolves.toEqual({
      books: [],
      page: 1,
      totalFound: 0,
      hasMore: false,
    });

    expect(fetch).not.toHaveBeenCalled();
  });

  it('serializes typed provider fields and pagination into the metadata request', async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          books: [],
          provider: 'hardcover',
          query: '',
          page: 2,
          total_found: 41,
          has_more: true,
        }),
      ),
    );
    vi.stubGlobal('fetch', fetch);

    await searchMetadata(
      '',
      40,
      'newest',
      { author: 'Ursula Le Guin', year: 1969, owned: true },
      2,
      'ebook',
      'hardcover',
    );

    const request = String(fetch.mock.calls[0]?.[0]);
    expect(request).toContain('sort=newest');
    expect(request).toContain('page=2');
    expect(request).toContain('provider=hardcover');
    expect(request).toContain('author=Ursula+Le+Guin');
    expect(request).toContain('year=1969');
    expect(request).toContain('owned=true');
  });
});
