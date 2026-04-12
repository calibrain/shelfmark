import { describe, it, expect } from 'vitest';

import { getConfiguredMetadataProviderForContentType } from '../utils/metadataProviders';

describe('metadataProviders', () => {
  it('uses the audiobook-specific provider when one is configured', () => {
    expect(
      getConfiguredMetadataProviderForContentType({
        contentType: 'audiobook',
        configuredMetadataProvider: 'openlibrary',
        configuredAudiobookMetadataProvider: 'hardcover',
      }),
    ).toBe('hardcover');
  });

  it('falls back to the main provider for audiobooks when needed', () => {
    expect(
      getConfiguredMetadataProviderForContentType({
        contentType: 'audiobook',
        configuredMetadataProvider: 'hardcover',
        configuredAudiobookMetadataProvider: null,
      }),
    ).toBe('hardcover');
  });

  it('uses the main provider for ebook searches', () => {
    expect(
      getConfiguredMetadataProviderForContentType({
        contentType: 'ebook',
        configuredMetadataProvider: 'hardcover',
        configuredAudiobookMetadataProvider: 'openlibrary',
      }),
    ).toBe('hardcover');
  });
});
