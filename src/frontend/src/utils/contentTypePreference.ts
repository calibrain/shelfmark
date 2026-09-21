import type { ContentType } from '../types';

export const CONTENT_TYPE_STORAGE_KEY = 'preferred-content-type';

export interface ContentTypePreference {
  contentType: ContentType;
  combinedMode: boolean;
}

/**
 * Resolve the content type the search page opens on.
 *
 * A value this browser stored wins, because it is the tab the user last picked.
 * Otherwise the server default applies, so an admin can decide what a first visit
 * shows and an audiobook-first instance does not open on the ebook tab.
 */
export const resolveContentTypePreference = (
  stored: string | null,
  serverDefault: string | null | undefined,
): ContentTypePreference => {
  if (stored === 'combined') {
    return { contentType: 'ebook', combinedMode: true };
  }
  if (stored === 'ebook' || stored === 'audiobook') {
    return { contentType: stored, combinedMode: false };
  }
  if (serverDefault === 'audiobook' || serverDefault === 'ebook') {
    return { contentType: serverDefault, combinedMode: false };
  }
  return { contentType: 'ebook', combinedMode: false };
};
