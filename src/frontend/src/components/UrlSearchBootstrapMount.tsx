import type { Dispatch, SetStateAction } from 'react';

import { useMountEffect } from '@/hooks/useMountEffect';
import type {
  AppConfig,
  AdvancedFilterState,
  ContentType,
  QueryTargetOption,
  SearchMode,
  SortOption,
} from '@/types';
import { buildSearchQuery } from '@/utils/buildSearchQuery';
import { resolveDefaultLanguageCodes } from '@/utils/languageFilters';
import { getEffectiveMetadataSort } from '@/utils/metadataSort';
import type { ParsedUrlSearch } from '@/utils/parseUrlSearchParams';

const ADVANCED_FILTER_VISIBILITY_KEYS = ['content', 'lang', 'formats'] as const;

interface UrlSearchBootstrapMountProps {
  parsedParams: ParsedUrlSearch;
  config: AppConfig;
  contentType: ContentType;
  combinedMode: boolean;
  combinedModeAllowed: boolean;
  queryTargets: QueryTargetOption[];
  advancedFilters: AdvancedFilterState;
  resolvedMetadataDefaultSort: string;
  resolvedMetadataSortOptions: SortOption[];
  setContentType: (value: ContentType) => void;
  setCombinedMode: (value: boolean) => void;
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

export const UrlSearchBootstrapMount = ({
  parsedParams,
  config,
  contentType,
  combinedMode,
  combinedModeAllowed,
  queryTargets,
  advancedFilters,
  resolvedMetadataDefaultSort,
  resolvedMetadataSortOptions,
  setContentType,
  setCombinedMode,
  setSearchInput,
  setAdvancedFilters,
  setShowAdvanced,
  setActiveQueryTarget,
  runSearchWithPolicyRefresh,
  onComplete,
}: UrlSearchBootstrapMountProps) => {
  useMountEffect(() => {
    onComplete();

    const parsedSearchMode = config.search_mode || 'universal';
    const urlContentTypeOverride =
      parsedSearchMode === 'universal' ? parsedParams.contentType : undefined;
    const urlForcesCombined =
      parsedSearchMode === 'universal' && parsedParams.combinedMode === true && combinedModeAllowed;

    if (urlContentTypeOverride && urlContentTypeOverride !== contentType) {
      setContentType(urlContentTypeOverride);
    }

    if (urlForcesCombined && !combinedMode) {
      setCombinedMode(true);
    } else if (urlContentTypeOverride && combinedMode) {
      setCombinedMode(false);
    }

    const urlSearchByOverride =
      parsedParams.searchBy && queryTargets.some((target) => target.key === parsedParams.searchBy)
        ? parsedParams.searchBy
        : undefined;

    // Search By target can be deep-linked on its own (e.g. `#search_by=manual`, no query),
    // so apply it even when there's nothing else to search for.
    if (urlSearchByOverride) {
      setActiveQueryTarget(urlSearchByOverride);
    }

    if (!parsedParams.hasSearchParams) {
      return;
    }

    const bookLanguages = config.book_languages || [];
    const defaultLanguageCodes = resolveDefaultLanguageCodes(
      config.default_language,
      bookLanguages,
    );

    if (parsedParams.searchInput) {
      setSearchInput(parsedParams.searchInput);
    }

    let nextQueryTarget = urlSearchByOverride || 'general';
    if (parsedSearchMode === 'direct' && !urlSearchByOverride) {
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
