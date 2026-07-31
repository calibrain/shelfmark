// @vitest-environment jsdom

import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ReleaseModal } from '../components/ReleaseModal';

const releaseResponse = {
  releases: [],
  book: { provider: 'hardcover', provider_id: 'provider-book-4', title: 'Requested Book' },
  sources_searched: ['prowlarr'],
};

describe('ReleaseModal request search', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('searches releases using the canonical Book identity from an Activity request', async () => {
    const fetch = vi.fn((url: string) => {
      if (url === '/api/release-sources') {
        return Promise.resolve(
          new Response(
            JSON.stringify([
              {
                name: 'prowlarr',
                display_name: 'Prowlarr',
                enabled: true,
                supported_content_types: ['ebook'],
              },
            ]),
          ),
        );
      }
      return Promise.resolve(new Response(JSON.stringify(releaseResponse)));
    });
    vi.stubGlobal('fetch', fetch);

    render(
      <ReleaseModal
        book={{
          id: '4',
          book_id: 4,
          provider: 'hardcover',
          provider_id: 'provider-book-4',
          title: 'Requested Book',
          author: 'Shelfmark',
        }}
        onClose={() => undefined}
        onDownload={async () => undefined}
        supportedFormats={['epub']}
        contentType="ebook"
        defaultLanguages={['en']}
        bookLanguages={[{ code: 'en', language: 'English' }]}
        currentStatus={{}}
      />,
    );

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/releases?library_book_id=4&source=prowlarr&content_type=ebook',
        expect.any(Object),
      );
    });
  });
});
