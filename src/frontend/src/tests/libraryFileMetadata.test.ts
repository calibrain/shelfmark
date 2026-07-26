import { describe, expect, it } from 'vitest';

import { formatFileSize } from '../library/types';

describe('available file metadata', () => {
  it('renders raw on-disk byte counts as readable file sizes', () => {
    expect(formatFileSize('779412')).toBe('761.1 KB');
  });

  it('preserves size labels supplied by release sources', () => {
    expect(formatFileSize('1.2 MB')).toBe('1.2 MB');
  });
});
