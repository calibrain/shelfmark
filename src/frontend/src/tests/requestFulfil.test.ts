import { describe, it, expect } from 'vitest';

import { bookFromRequestData } from '../utils/requestFulfil';

describe('requestFulfil.bookFromRequestData', () => {
  it('maps request book data into a ReleaseModal-compatible Book object', () => {
    const book = bookFromRequestData({
      title: 'The Pragmatic Programmer',
      author: 'Andrew Hunt',
      source: 'direct_download',
      provider: 'openlibrary',
      provider_id: 'ol-123',
      preview: 'https://example.com/cover.jpg',
      year: 1999,
      series_name: 'Pragmatic Classics',
      series_position: '1',
      subtitle: 'From Journeyman to Master',
      source_url: 'https://openlibrary.org/books/ol-123',
    });

    expect(book.id).toBe('ol-123');
    expect(book.title).toBe('The Pragmatic Programmer');
    expect(book.author).toBe('Andrew Hunt');
    expect(book.source).toBe('direct_download');
    expect(book.provider).toBe('openlibrary');
    expect(book.provider_id).toBe('ol-123');
    expect(book.preview).toBe('https://example.com/cover.jpg');
    expect(book.year).toBe('1999');
    expect(book.series_name).toBe('Pragmatic Classics');
    expect(book.series_position).toBe(1);
    expect(book.subtitle).toBe('From Journeyman to Master');
    expect(book.source_url).toBe('https://openlibrary.org/books/ol-123');
  });

  it('provides safe fallbacks when request payload fields are missing', () => {
    const book = bookFromRequestData({});

    expect(book.id).toBe('Unknown title');
    expect(book.title).toBe('Unknown title');
    expect(book.author).toBe('Unknown author');
    expect(book.provider).toBe(undefined);
    expect(book.provider_id).toBe(undefined);
    expect(book.series_position).toBe(undefined);
  });
});
