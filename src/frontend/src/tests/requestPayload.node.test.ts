import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Book, CreateRequestPayload, Release } from '../types/index.js';
import {
  buildDirectRequestPayload,
  buildMetadataBookRequestData,
  buildReleaseDataFromDirectBook,
  buildReleaseDataFromMetadataRelease,
  getBrowseSource,
  isSourceBackedRequestPayload,
  getRequestSuccessMessage,
  toContentType,
} from '../utils/requestPayload.js';

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
    assert.equal(toContentType('audiobook'), 'audiobook');
    assert.equal(toContentType('AUDIOBOOK'), 'audiobook');
    assert.equal(toContentType('ebook'), 'ebook');
    assert.equal(toContentType('something-else'), 'ebook');
  });

  it('creates direct request payload as release-level with attached release data', () => {
    const payload = buildDirectRequestPayload(baseBook);

    assert.equal(payload.context.request_level, 'release');
    assert.equal(payload.context.source, 'direct_download');
    assert.equal(payload.context.content_type, 'ebook');
    assert.ok(payload.release_data);
    assert.equal(payload.release_data?.source, 'direct_download');
    assert.equal(payload.release_data?.search_mode, 'direct');
    assert.equal(isSourceBackedRequestPayload(payload), true);
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
      'ebook'
    );

    assert.equal(bookData.provider, 'openlibrary');
    assert.equal(bookData.provider_id, 'ol-1');
    assert.equal(bookData.content_type, 'ebook');
    assert.equal(releaseData.source, 'prowlarr');
    assert.equal(releaseData.format, 'epub');
    assert.equal(releaseData.content_type, 'ebook');
    assert.equal(directReleaseData.search_mode, 'direct');
    assert.equal(sourceBackedReleaseData.search_mode, 'direct');
  });

  it('resolves browse source from source-backed or provider-backed books', () => {
    assert.equal(getBrowseSource(baseBook), 'direct_download');
    assert.equal(
      getBrowseSource({
        ...baseBook,
        source: undefined,
        provider: 'direct_download',
      }),
      'direct_download'
    );
  });

  it('throws when browse-source context is missing', () => {
    assert.throws(
      () => getBrowseSource({
        id: 'missing-source',
        title: 'Example',
        author: 'Author',
      }),
      /missing source context/
    );
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

    assert.equal(getRequestSuccessMessage(payloadWithBookTitle), 'Request submitted: Book From Metadata');
    assert.equal(getRequestSuccessMessage(payloadWithReleaseTitleOnly), 'Request submitted: Release Only Title');
    assert.equal(getRequestSuccessMessage(payloadUntitled), 'Request submitted: Untitled');
  });
});
