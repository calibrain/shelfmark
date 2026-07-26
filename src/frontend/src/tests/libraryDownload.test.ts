import { afterEach, describe, expect, it, vi } from 'vitest';

import { downloadLibraryFile } from '../services/api';

describe('library file downloads', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the server attachment filename for Blob downloads', async () => {
    const link = { href: '', download: '', click: vi.fn() };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(new Blob(['book']), {
          headers: { 'Content-Disposition': 'attachment; filename="Book 1 - Author A.epub"' },
        }),
      ),
    );
    vi.stubGlobal('document', { createElement: vi.fn().mockReturnValue(link) });
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn().mockReturnValue('blob:book'),
      revokeObjectURL: vi.fn(),
    });

    await downloadLibraryFile(1, { format: 'epub' });

    expect(link.download).toBe('Book 1 - Author A.epub');
    expect(link.click).toHaveBeenCalledOnce();
  });
});
