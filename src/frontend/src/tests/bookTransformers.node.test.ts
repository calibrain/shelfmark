import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { isMetadataBook, type Release } from '../types/index.js';
import { transformReleaseToDirectBook, transformSourceRecordToBook } from '../utils/bookTransformers.js';

describe('bookTransformers.transformReleaseToDirectBook', () => {
  it('maps direct release data into the direct-mode book card shape', () => {
    const release: Release = {
      source: 'direct_download',
      source_id: 'md5-123',
      title: 'Example Title',
      format: 'epub',
      language: 'en',
      size: '2 MB',
      info_url: 'https://example.com/md5/md5-123',
      extra: {
        author: 'Example Author',
        year: '2001',
        preview: 'https://example.com/cover.jpg',
        publisher: 'Example Publisher',
        description: 'Example description',
        info: {
          Downloads: ['42'],
        },
      },
    };

    const book = transformReleaseToDirectBook(release);

    assert.equal(book.id, 'md5-123');
    assert.equal(book.title, 'Example Title');
    assert.equal(book.author, 'Example Author');
    assert.equal(book.year, '2001');
    assert.equal(book.language, 'en');
    assert.equal(book.format, 'epub');
    assert.equal(book.size, '2 MB');
    assert.equal(book.preview, 'https://example.com/cover.jpg');
    assert.equal(book.publisher, 'Example Publisher');
    assert.equal(book.description, 'Example description');
    assert.equal(book.source, 'direct_download');
    assert.equal(book.provider, 'direct_download');
    assert.equal(book.provider_id, 'md5-123');
    assert.equal(book.provider_display_name, 'Direct Download');
    assert.equal(book.source_url, 'https://example.com/md5/md5-123');
    assert.deepEqual(book.info, { Downloads: ['42'] });
    assert.equal(isMetadataBook(book), false);
  });
});

describe('bookTransformers.transformSourceRecordToBook', () => {
  it('maps source-native records into source-backed book context', () => {
    const book = transformSourceRecordToBook({
      id: 'md5-456',
      title: 'Record Title',
      source: 'direct_download',
      author: 'Record Author',
      preview: '/api/covers/md5-456',
      year: 1999,
      language: 'en',
      format: 'epub',
      size: '3 MB',
      publisher: 'Record Publisher',
      description: 'Record description',
      info: {
        Downloads: ['64'],
      },
      source_url: 'https://example.com/record/md5-456',
    });

    assert.equal(book.id, 'md5-456');
    assert.equal(book.title, 'Record Title');
    assert.equal(book.author, 'Record Author');
    assert.equal(book.source, 'direct_download');
    assert.equal(book.provider, 'direct_download');
    assert.equal(book.provider_id, 'md5-456');
    assert.equal(book.provider_display_name, 'Direct Download');
    assert.equal(book.year, '1999');
    assert.equal(book.preview, '/api/covers/md5-456');
    assert.equal(book.source_url, 'https://example.com/record/md5-456');
    assert.deepEqual(book.info, { Downloads: ['64'] });
    assert.equal(isMetadataBook(book), false);
  });
});
