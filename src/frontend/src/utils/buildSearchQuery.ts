import type { AdvancedFilterState, Language } from '../types';

interface BuildSearchQueryOptions {
  searchInput: string;
  showAdvanced: boolean;
  advancedFilters: AdvancedFilterState;
  bookLanguages: Language[];
  defaultLanguage: string[];
}

export const buildSearchQuery = ({
  searchInput,
  showAdvanced: _showAdvanced,
  advancedFilters,
  bookLanguages: _bookLanguages,
  defaultLanguage: _defaultLanguage,
}: BuildSearchQueryOptions): string => {
  const queryParts: string[] = [];

  const basic = searchInput.trim();
  if (basic) {
    queryParts.push(`query=${encodeURIComponent(basic)}`);
  }

  if (advancedFilters.sort) {
    queryParts.push(`sort=${encodeURIComponent(advancedFilters.sort)}`);
  }

  return queryParts.join('&');
};
