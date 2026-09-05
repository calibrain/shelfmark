import type { AdvancedFilterState, ContentType } from '../types';

/**
 * Parsed search parameters from URL
 */
export interface ParsedUrlSearch {
  searchInput: string;
  advancedFilters: Partial<AdvancedFilterState>;
  contentType?: ContentType;
  combinedMode?: boolean;
  /**
   * "Search By" target (e.g. general/author/title/series/manual), from the `search_by`
   * param. Kept verbatim - provider field keys are matched case-insensitively against
   * the live targets, so a custom provider's camelCase key still resolves.
   */
  searchBy?: string;
  hasSearchParams: boolean;
}

const parseContentTypeParam = (
  value: string | null,
): { contentType?: ContentType; combinedMode?: true } => {
  if (!value) {
    return {};
  }
  const normalized = value.trim().toLowerCase();
  if (normalized === 'ebook' || normalized === 'audiobook') {
    return { contentType: normalized };
  }
  if (normalized === 'combined') {
    return { combinedMode: true };
  }
  return {};
};

/**
 * Parse URL search parameters into search state.
 *
 * Supports both Direct Download and Universal mode parameters.
 * In Universal mode, query/sort are used for search text, and content_type
 * selects ebook, audiobook, or combined (search both at once).
 *
 * Params live in the URL hash (legacy query strings are still accepted and
 * rewritten to a hash on load - see useUrlSearch).
 *
 * @example
 * // Direct mode: /#q=harry+potter&author=rowling&format=epub&lang=en
 * // Universal mode: /#q=dune&sort=popularity
 * // Universal combined: /#q=dune&content_type=combined
 * // Search By deep link: /#search_by=manual&q=dune
 */
export function parseUrlSearchParams(searchParams: URLSearchParams): ParsedUrlSearch {
  const contentTypeParam = parseContentTypeParam(
    searchParams.get('content_type') || searchParams.get('contentType'),
  );

  const searchByParam = (searchParams.get('search_by') || '').trim();

  const result: ParsedUrlSearch = {
    searchInput: '',
    advancedFilters: {},
    contentType: contentTypeParam.contentType,
    combinedMode: contentTypeParam.combinedMode,
    searchBy: searchByParam || undefined,
    hasSearchParams: false,
  };

  // Parse main query (support both 'q' and 'query' for flexibility)
  const query = searchParams.get('q') || searchParams.get('query') || '';
  if (query) {
    result.searchInput = query;
    result.hasSearchParams = true;
  }

  // Parse single-value filters
  const isbn = searchParams.get('isbn');
  const author = searchParams.get('author');
  const title = searchParams.get('title');
  const sort = searchParams.get('sort');
  const content = searchParams.get('content');

  if (isbn) {
    result.advancedFilters.isbn = isbn;
    result.hasSearchParams = true;
  }
  if (author) {
    result.advancedFilters.author = author;
    result.hasSearchParams = true;
  }
  if (title) {
    result.advancedFilters.title = title;
    result.hasSearchParams = true;
  }
  if (sort) {
    result.advancedFilters.sort = sort;
    result.hasSearchParams = true;
  }
  if (content) {
    result.advancedFilters.content = content;
    result.hasSearchParams = true;
  }

  // Parse multi-value filters (can appear multiple times in URL)
  // e.g., /?lang=en&lang=de or /?format=epub&format=mobi
  const langValues = searchParams.getAll('lang').filter(Boolean);
  const formatValues = searchParams.getAll('format').filter(Boolean);

  if (langValues.length > 0) {
    result.advancedFilters.lang = langValues;
    result.hasSearchParams = true;
  }
  if (formatValues.length > 0) {
    result.advancedFilters.formats = formatValues;
    result.hasSearchParams = true;
  }

  return result;
}
