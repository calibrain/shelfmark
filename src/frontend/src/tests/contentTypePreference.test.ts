import { describe, expect, it } from 'vitest';

import { resolveContentTypePreference } from '../utils/contentTypePreference';

describe('resolveContentTypePreference', () => {
  it('keeps the tab this browser last picked', () => {
    expect(resolveContentTypePreference('audiobook', 'ebook')).toEqual({
      contentType: 'audiobook',
      combinedMode: false,
    });
    expect(resolveContentTypePreference('ebook', 'audiobook')).toEqual({
      contentType: 'ebook',
      combinedMode: false,
    });
  });

  it('keeps combined mode, which is stored as its own value', () => {
    expect(resolveContentTypePreference('combined', 'audiobook')).toEqual({
      contentType: 'ebook',
      combinedMode: true,
    });
  });

  it('uses the server default when this browser has stored nothing', () => {
    expect(resolveContentTypePreference(null, 'audiobook')).toEqual({
      contentType: 'audiobook',
      combinedMode: false,
    });
  });

  it('falls back to ebook when there is no stored value and no server default', () => {
    expect(resolveContentTypePreference(null, null)).toEqual({
      contentType: 'ebook',
      combinedMode: false,
    });
    expect(resolveContentTypePreference(null, undefined)).toEqual({
      contentType: 'ebook',
      combinedMode: false,
    });
  });

  it('ignores a stored or configured value it does not recognise', () => {
    expect(resolveContentTypePreference('comic', 'audiobook')).toEqual({
      contentType: 'audiobook',
      combinedMode: false,
    });
    expect(resolveContentTypePreference(null, 'comic')).toEqual({
      contentType: 'ebook',
      combinedMode: false,
    });
  });
});
