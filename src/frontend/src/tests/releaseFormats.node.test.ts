import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { getReleaseFormats } from '../utils/releaseFormats.js';
import type { Release } from '../types';

function buildRelease(overrides: Partial<Release>): Release {
  return {
    source: 'prowlarr',
    source_id: 'release-1',
    title: 'Test Release',
    ...overrides,
  };
}

describe('releaseFormats.getReleaseFormats', () => {
  it('returns primary and extra formats in order, normalized and deduplicated', () => {
    const release = buildRelease({
      format: 'AZW3',
      extra: { formats: ['MOBI', 'EPUB', 'azw3', '  mobi  '] },
    });

    assert.deepEqual(getReleaseFormats(release), ['azw3', 'mobi', 'epub']);
  });

  it('uses extra formats when primary format is missing', () => {
    const release = buildRelease({
      extra: { formats: ['EPUB', 'MOBI'] },
    });

    assert.deepEqual(getReleaseFormats(release), ['epub', 'mobi']);
  });

  it('supports legacy string value in extra.formats', () => {
    const release = buildRelease({
      extra: { formats: 'PDF' },
    });

    assert.deepEqual(getReleaseFormats(release), ['pdf']);
  });
});
