import type { AdvancedFilterState, ContentType } from '../types';

export interface UrlSearchHashState {
  searchInput: string;
  searchBy: string;
  contentType: ContentType;
  combinedMode: boolean;
  advancedFilters: Partial<AdvancedFilterState>;
}

/**
 * Build the URL hash fragment (without the leading `#`) that mirrors the
 * current search state, using the same param names parseUrlSearchParams reads.
 *
 * Kept as a hash fragment (not query params) so it stays browser-side only,
 * rather than looking like a server-processed query string.
 */
export const buildUrlSearchHash = (state: UrlSearchHashState): string => {
  const params = new URLSearchParams();

  if (state.searchInput) {
    params.set('q', state.searchInput);
  }
  if (state.searchBy && state.searchBy !== 'general') {
    params.set('search_by', state.searchBy);
  }
  if (state.combinedMode) {
    params.set('content_type', 'combined');
  } else if (state.contentType && state.contentType !== 'ebook') {
    // 'ebook' is the app's default content type - omit it like 'general' search_by,
    // so a plain default-state URL doesn't carry a hash at all.
    params.set('content_type', state.contentType);
  }

  const { isbn, author, title, sort, content, lang, formats } = state.advancedFilters;
  if (isbn) params.set('isbn', isbn);
  if (author) params.set('author', author);
  if (title) params.set('title', title);
  if (sort) params.set('sort', sort);
  if (content) params.set('content', content);
  for (const value of lang ?? []) {
    if (value) params.append('lang', value);
  }
  for (const value of formats ?? []) {
    if (value) params.append('format', value);
  }

  return params.toString();
};
