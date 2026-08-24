import { describe, it, expect } from 'vitest';

import type { Release } from '../types';
import { getReleaseFormats, getUnrecognizedReleaseFormats } from '../utils/releaseFormats';

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

    expect(getReleaseFormats(release)).toEqual(['azw3', 'mobi', 'epub']);
  });

  it('uses extra formats when primary format is missing', () => {
    const release = buildRelease({
      extra: { formats: ['EPUB', 'MOBI'] },
    });

    expect(getReleaseFormats(release)).toEqual(['epub', 'mobi']);
  });

  it('supports legacy string value in extra.formats', () => {
    const release = buildRelease({
      extra: { formats: 'PDF' },
    });

    expect(getReleaseFormats(release)).toEqual(['pdf']);
  });
});

describe('releaseFormats.getUnrecognizedReleaseFormats', () => {
  it('returns normalized, deduplicated unrecognized formats from extra', () => {
    const release = buildRelease({
      extra: { unrecognized_formats: ['MP4', ' mp4 ', 'WEBM'] },
    });

    expect(getUnrecognizedReleaseFormats(release)).toEqual(['mp4', 'webm']);
  });

  it('accepts a single string value', () => {
    const release = buildRelease({ extra: { unrecognized_formats: 'MP4' } });

    expect(getUnrecognizedReleaseFormats(release)).toEqual(['mp4']);
  });

  it('returns an empty list when nothing was flagged', () => {
    expect(getUnrecognizedReleaseFormats(buildRelease({}))).toEqual([]);
    expect(
      getUnrecognizedReleaseFormats(buildRelease({ extra: { unrecognized_formats: null } })),
    ).toEqual([]);
  });
});
