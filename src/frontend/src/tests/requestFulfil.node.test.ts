import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { bookFromRequestData } from '../utils/requestFulfil.js';

describe('requestFulfil.bookFromRequestData', () => {
  it('maps request book data into a ReleaseModal-compatible Book object', () => {
    const book = bookFromRequestData({
      title: 'The Pragmatic Programmer',
      author: 'Andrew Hunt',
      provider: 'openlibrary',
      provider_id: 'ol-123',
      preview: 'https://example.com/cover.jpg',
      year: 1999,
      series_name: 'Pragmatic Classics',
      series_position: '1',
      subtitle: 'From Journeyman to Master',
      source_url: 'https://openlibrary.org/books/ol-123',
    });

    assert.equal(book.id, 'ol-123');
    assert.equal(book.title, 'The Pragmatic Programmer');
    assert.equal(book.author, 'Andrew Hunt');
    assert.equal(book.provider, 'openlibrary');
    assert.equal(book.provider_id, 'ol-123');
    assert.equal(book.preview, 'https://example.com/cover.jpg');
    assert.equal(book.year, '1999');
    assert.equal(book.series_name, 'Pragmatic Classics');
    assert.equal(book.series_position, 1);
    assert.equal(book.subtitle, 'From Journeyman to Master');
    assert.equal(book.source_url, 'https://openlibrary.org/books/ol-123');
  });

  it('provides safe fallbacks when request payload fields are missing', () => {
    const book = bookFromRequestData({});

    assert.equal(book.id, 'Unknown title');
    assert.equal(book.title, 'Unknown title');
    assert.equal(book.author, 'Unknown author');
    assert.equal(book.provider, undefined);
    assert.equal(book.provider_id, undefined);
    assert.equal(book.series_position, undefined);
  });
});
