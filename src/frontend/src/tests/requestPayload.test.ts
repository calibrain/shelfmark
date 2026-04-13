import { describe, it, expect } from 'vitest';

import type { Book, CreateRequestPayload, Release } from '../types/index';
import {
  buildDirectRequestPayload,
  buildMetadataBookRequestData,
  buildReleaseDataFromDirectBook,
  buildReleaseDataFromMetadataRelease,
  getBrowseSource,
  isSourceBackedRequestPayload,
  getRequestSuccessMessage,
  toContentType,
} from '../utils/requestPayload';

const baseBook: Book = {
  id: 'book-1',
  title: 'Example Title',
  author: 'Example Author',
  provider: 'openlibrary',
  provider_id: 'ol-1',
  source: 'direct_download',
  preview: 'https://example.com/cover.jpg',
};

const baseRelease: Release = {
  source: 'prowlarr',
  source_id: 'release-1',
  title: 'Example Title [EPUB]',
  format: 'epub',
  size: '2 MB',
};

describe('requestPayload utilities', () => {
  it('normalizes content type values', () => {
    expect(toContentType('audiobook')).toBe('audiobook');
    expect(toContentType('AUDIOBOOK')).toBe('audiobook');
    expect(toContentType('ebook')).toBe('ebook');
    expect(toContentType('something-else')).toBe('ebook');
  });

  it('creates direct request payload as release-level with attached release data', () => {
    const payload = buildDirectRequestPayload(baseBook);

    expect(payload.context.request_level).toBe('release');
    expect(payload.context.source).toBe('direct_download');
    expect(payload.context.content_type).toBe('ebook');
    expect(payload.release_data).toBeTruthy();
    expect(payload.release_data?.source).toBe('direct_download');
    expect(payload.release_data?.search_mode).toBe('direct');
    expect(isSourceBackedRequestPayload(payload)).toBe(true);
  });

  it('builds metadata book + release payload fragments', () => {
    const bookData = buildMetadataBookRequestData(baseBook, 'ebook');
    const releaseData = buildReleaseDataFromMetadataRelease(baseBook, baseRelease, 'ebook');
    const directReleaseData = buildReleaseDataFromDirectBook(baseBook);
    const sourceBackedReleaseData = buildReleaseDataFromMetadataRelease(
      {
        ...baseBook,
        provider: 'direct_download',
        provider_id: 'dd-1',
        source: 'direct_download',
      },
      {
        ...baseRelease,
        source: 'direct_download',
      },
      'ebook',
    );

    expect(bookData.provider).toBe('openlibrary');
    expect(bookData.provider_id).toBe('ol-1');
    expect(bookData.content_type).toBe('ebook');
    expect(releaseData.source).toBe('prowlarr');
    expect(releaseData.format).toBe('epub');
    expect(releaseData.content_type).toBe('ebook');
    expect(directReleaseData.search_mode).toBe('direct');
    expect(sourceBackedReleaseData.search_mode).toBe('direct');
  });

  it('resolves browse source from source-backed or provider-backed books', () => {
    expect(getBrowseSource(baseBook)).toBe('direct_download');
    expect(
      getBrowseSource({
        ...baseBook,
        source: undefined,
        provider: 'direct_download',
      }),
    ).toBe('direct_download');
  });

  it('throws when browse-source context is missing', () => {
    expect(() =>
      getBrowseSource({
        id: 'missing-source',
        title: 'Example',
        author: 'Author',
      }),
    ).toThrow(/missing source context/);
  });

  it('builds success toast message from payload title with fallback', () => {
    const payloadWithBookTitle: CreateRequestPayload = {
      book_data: { title: 'Book From Metadata' },
      release_data: { title: 'Book From Release' },
      context: {
        source: 'prowlarr',
        content_type: 'ebook',
        request_level: 'release',
      },
    };

    const payloadWithReleaseTitleOnly: CreateRequestPayload = {
      book_data: {},
      release_data: { title: 'Release Only Title' },
      context: {
        source: 'prowlarr',
        content_type: 'ebook',
        request_level: 'release',
      },
    };

    const payloadUntitled: CreateRequestPayload = {
      book_data: {},
      release_data: {},
      context: {
        source: 'prowlarr',
        content_type: 'ebook',
        request_level: 'release',
      },
    };

    expect(getRequestSuccessMessage(payloadWithBookTitle)).toBe(
      'Request submitted: Book From Metadata',
    );
    expect(getRequestSuccessMessage(payloadWithReleaseTitleOnly)).toBe(
      'Request submitted: Release Only Title',
    );
    expect(getRequestSuccessMessage(payloadUntitled)).toBe('Request submitted: Untitled');
  });
});
