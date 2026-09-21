import { describe, expect, it } from 'vitest';

import { resolveContentTypePreference } from '../utils/contentTypePreference';

describe('resolveContentTypePreference', () => {
  it('keeps the tab this browser chose, over any configured default', () => {
    expect(resolveContentTypePreference('audiobook', 'ebook')).toEqual({
      contentType: 'audiobook',
      combinedMode: false,
      source: 'chosen',
    });
    expect(resolveContentTypePreference('ebook', 'audiobook')).toEqual({
      contentType: 'ebook',
      combinedMode: false,
      source: 'chosen',
    });
  });

  it('keeps combined mode, which is stored as its own value', () => {
    expect(resolveContentTypePreference('combined', 'audiobook')).toEqual({
      contentType: 'ebook',
      combinedMode: true,
      source: 'chosen',
    });
  });

  it('uses the configured default when this browser has chosen nothing', () => {
    expect(resolveContentTypePreference(null, 'audiobook')).toEqual({
      contentType: 'audiobook',
      combinedMode: false,
      source: 'unset',
    });
  });

  it('leaves a configured default open to being overridden', () => {
    // 'unset' is what lets a deep-linked content type and a later setting change win.
    expect(resolveContentTypePreference(null, 'audiobook').source).toBe('unset');
    expect(resolveContentTypePreference('audiobook', null).source).toBe('chosen');
  });

  it('falls back to ebook with neither a stored choice nor a default', () => {
    expect(resolveContentTypePreference(null, null)).toEqual({
      contentType: 'ebook',
      combinedMode: false,
      source: 'unset',
    });
    expect(resolveContentTypePreference(null, undefined).contentType).toBe('ebook');
  });

  it('ignores a stored or configured value it does not recognise', () => {
    expect(resolveContentTypePreference('comic', 'audiobook')).toEqual({
      contentType: 'audiobook',
      combinedMode: false,
      source: 'unset',
    });
    expect(resolveContentTypePreference(null, 'comic')).toEqual({
      contentType: 'ebook',
      combinedMode: false,
      source: 'unset',
    });
  });
});
