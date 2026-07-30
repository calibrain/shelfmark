import { afterEach, describe, expect, it, vi } from 'vitest';

import { getLibraryBooks } from '../services/api';

describe('library scope requests', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('serializes an all-library scope request', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ books: [] })));
    vi.stubGlobal('fetch', fetch);

    await getLibraryBooks('all');

    expect(fetch).toHaveBeenCalledWith('/api/library/books?scope=all', expect.any(Object));
  });
});
