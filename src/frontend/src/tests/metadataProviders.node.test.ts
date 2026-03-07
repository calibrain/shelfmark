import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { getConfiguredMetadataProviderForContentType } from '../utils/metadataProviders.js';

describe('metadataProviders', () => {
  it('uses the audiobook-specific provider when one is configured', () => {
    assert.equal(
      getConfiguredMetadataProviderForContentType({
        contentType: 'audiobook',
        configuredMetadataProvider: 'openlibrary',
        configuredAudiobookMetadataProvider: 'hardcover',
      }),
      'hardcover',
    );
  });

  it('falls back to the main provider for audiobooks when needed', () => {
    assert.equal(
      getConfiguredMetadataProviderForContentType({
        contentType: 'audiobook',
        configuredMetadataProvider: 'hardcover',
        configuredAudiobookMetadataProvider: null,
      }),
      'hardcover',
    );
  });

  it('uses the main provider for ebook searches', () => {
    assert.equal(
      getConfiguredMetadataProviderForContentType({
        contentType: 'ebook',
        configuredMetadataProvider: 'hardcover',
        configuredAudiobookMetadataProvider: 'openlibrary',
      }),
      'hardcover',
    );
  });
});
