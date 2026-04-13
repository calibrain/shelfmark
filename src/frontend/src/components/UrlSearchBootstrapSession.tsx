import type { Dispatch, SetStateAction } from 'react';

import { useMountEffect } from '@/hooks/useMountEffect';
import type { AppConfig, AdvancedFilterState, ContentType, SearchMode, SortOption } from '@/types';
import { buildSearchQuery } from '@/utils/buildSearchQuery';
import { getEffectiveMetadataSort } from '@/utils/metadataSort';
import type { ParsedUrlSearch } from '@/utils/parseUrlSearchParams';

const ADVANCED_FILTER_VISIBILITY_KEYS = ['content', 'lang', 'formats'] as const;

interface UrlSearchBootstrapSessionProps {
  parsedParams: ParsedUrlSearch;
  config: AppConfig;
  contentType: ContentType;
  advancedFilters: AdvancedFilterState;
  resolvedMetadataDefaultSort: string;
  resolvedMetadataSortOptions: SortOption[];
  setContentType: (value: ContentType) => void;
  setSearchInput: (value: string) => void;
  setAdvancedFilters: Dispatch<SetStateAction<AdvancedFilterState>>;
  setShowAdvanced: (value: boolean) => void;
  setActiveQueryTarget: (value: string) => void;
  runSearchWithPolicyRefresh: (opts: {
    query: string;
    contentTypeOverride?: ContentType;
    searchModeOverride?: SearchMode;
  }) => void;
  onComplete: () => void;
}

export const UrlSearchBootstrapSession = ({
  parsedParams,
  config,
  contentType,
  advancedFilters,
  resolvedMetadataDefaultSort,
  resolvedMetadataSortOptions,
  setContentType,
  setSearchInput,
  setAdvancedFilters,
  setShowAdvanced,
  setActiveQueryTarget,
  runSearchWithPolicyRefresh,
  onComplete,
}: UrlSearchBootstrapSessionProps) => {
  useMountEffect(() => {
    onComplete();

    const parsedSearchMode = config.search_mode || 'direct';
    const urlContentTypeOverride =
      parsedSearchMode === 'universal' ? parsedParams.contentType : undefined;

    if (urlContentTypeOverride && urlContentTypeOverride !== contentType) {
      setContentType(urlContentTypeOverride);
    }

    if (!parsedParams.hasSearchParams) {
      return;
    }

    const bookLanguages = config.book_languages || [];
    const defaultLanguageCodes =
      config.default_language && config.default_language.length > 0
        ? config.default_language
        : [bookLanguages[0]?.code || 'en'];

    if (parsedParams.searchInput) {
      setSearchInput(parsedParams.searchInput);
    }

    let nextQueryTarget = 'general';
    if (parsedSearchMode === 'direct') {
      if (parsedParams.advancedFilters.isbn) {
        nextQueryTarget = 'isbn';
      } else if (parsedParams.advancedFilters.author) {
        nextQueryTarget = 'author';
      } else if (parsedParams.advancedFilters.title) {
        nextQueryTarget = 'title';
      }
    }
    setActiveQueryTarget(nextQueryTarget);

    const resolvedUrlMetadataSort =
      parsedSearchMode === 'universal'
        ? getEffectiveMetadataSort({
            currentSort:
              typeof parsedParams.advancedFilters.sort === 'string'
                ? parsedParams.advancedFilters.sort
                : '',
            defaultSort: resolvedMetadataDefaultSort,
            sortOptions: resolvedMetadataSortOptions,
          })
        : parsedParams.advancedFilters.sort;

    if (Object.keys(parsedParams.advancedFilters).length > 0) {
      setAdvancedFilters((prev) => ({
        ...prev,
        ...parsedParams.advancedFilters,
        ...(parsedSearchMode === 'universal' && resolvedUrlMetadataSort
          ? { sort: resolvedUrlMetadataSort }
          : {}),
      }));

      const hasAdvancedValues = ADVANCED_FILTER_VISIBILITY_KEYS.some((key) => {
        const value = parsedParams.advancedFilters[key];
        return Array.isArray(value) ? value.length > 0 : Boolean(value);
      });
      if (hasAdvancedValues) {
        setShowAdvanced(true);
      }
    }

    const mergedFilters: AdvancedFilterState = {
      ...advancedFilters,
      ...parsedParams.advancedFilters,
      ...(parsedSearchMode === 'universal' && resolvedUrlMetadataSort
        ? { sort: resolvedUrlMetadataSort }
        : {}),
    };

    const query = buildSearchQuery({
      searchInput:
        parsedSearchMode === 'direct' && nextQueryTarget !== 'general'
          ? ''
          : parsedParams.searchInput,
      showAdvanced: true,
      advancedFilters: {
        ...mergedFilters,
        isbn: nextQueryTarget === 'isbn' ? parsedParams.advancedFilters.isbn || '' : '',
        author: nextQueryTarget === 'author' ? parsedParams.advancedFilters.author || '' : '',
        title: nextQueryTarget === 'title' ? parsedParams.advancedFilters.title || '' : '',
      },
      bookLanguages,
      defaultLanguage: defaultLanguageCodes,
      searchMode: parsedSearchMode,
    });

    runSearchWithPolicyRefresh({
      query,
      contentTypeOverride: urlContentTypeOverride,
      searchModeOverride: parsedSearchMode,
    });
  });

  return null;
};
