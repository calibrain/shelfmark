import { describe, expect, it } from 'vitest';

import { getSizedCoverUrl } from '../utils/covers';

describe('getSizedCoverUrl', () => {
  it('adds size and format params to local cover proxy URLs', () => {
    expect(
      getSizedCoverUrl('/api/covers/book-1?url=abc', {
        width: 120,
        height: 180,
      }),
    ).toBe('/api/covers/book-1?url=abc&w=120&h=180&format=webp');
  });

  it('leaves external preview URLs alone', () => {
    expect(
      getSizedCoverUrl('https://covers.example.com/book.jpg', {
        width: 120,
        height: 180,
      }),
    ).toBe('https://covers.example.com/book.jpg');
  });

  it('preserves absolute proxy URLs', () => {
    expect(
      getSizedCoverUrl('https://bookrequest.example.com/api/covers/book-1?url=abc', {
        width: 56,
        height: 56,
        format: 'png',
      }),
    ).toBe('https://bookrequest.example.com/api/covers/book-1?url=abc&w=56&h=56&format=png');
  });
});
