// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ReleaseModal } from '../components/ReleaseModal';

const releaseResponse = {
  releases: [],
  book: { provider: 'hardcover', provider_id: 'provider-book-4', title: 'Requested Book' },
  sources_searched: ['prowlarr'],
};

const manualReleaseResponse = {
  ...releaseResponse,
  releases: [
    {
      source: 'prowlarr',
      source_id: 'manual-release',
      title: 'Manual result',
      format: 'epub',
      size: '1 MB',
    },
  ],
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

  it('searches manually and selects the resulting release for fulfilment', async () => {
    const onDownload = vi.fn().mockResolvedValue(undefined);
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
      return Promise.resolve(new Response(JSON.stringify(manualReleaseResponse)));
    });
    vi.stubGlobal('fetch', fetch);

    render(
      <ReleaseModal
        book={{
          id: '5',
          book_id: 5,
          provider: 'hardcover',
          provider_id: 'provider-book-5',
          title: 'Requested Book',
          author: 'Shelfmark',
        }}
        onClose={() => undefined}
        onDownload={onDownload}
        supportedFormats={['epub']}
        contentType="ebook"
        defaultLanguages={['en']}
        bookLanguages={[{ code: 'en', language: 'English' }]}
        currentStatus={{}}
        allowManualQuery
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Manual search query' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Custom search query' }), {
      target: { value: 'Exact title' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/releases?library_book_id=5&source=prowlarr&content_type=ebook&manual_query=Exact+title',
        expect.any(Object),
      );
    });
    fireEvent.click((await screen.findAllByRole('button', { name: 'Download Manual result' }))[0]);
    await waitFor(() => {
      expect(onDownload).toHaveBeenCalledWith(
        expect.objectContaining({ book_id: 5 }),
        expect.objectContaining({ source_id: 'manual-release' }),
        'ebook',
      );
    });
  });
});
