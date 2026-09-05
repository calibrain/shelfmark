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
import { findQueryTarget } from '@/utils/queryTargets';

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
  setSearchFieldValue: (key: string, value: string | number | boolean, label?: string) => void;
  runSearchWithPolicyRefresh: (opts: {
    query: string;
    contentTypeOverride?: ContentType;
    searchModeOverride?: SearchMode;
    fieldValues?: Record<string, string | number | boolean>;
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
  setSearchFieldValue,
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

    const urlSearchByTarget = findQueryTarget(queryTargets, parsedParams.searchBy);
    const urlSearchByOverride = urlSearchByTarget?.key;

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

    // Route `q` through the active target, mirroring how the live search dispatch reads it:
    // direct fields and text fields are typed into searchInput, other provider fields are
    // dispatched as fieldValues. Legacy links carry the value under the field's own param
    // (`?author=herbert`) instead, so fall back to that.
    const targetKey = urlSearchByTarget?.field?.key ?? nextQueryTarget;
    const legacyDirectValue =
      targetKey === 'isbn' || targetKey === 'author' || targetKey === 'title'
        ? parsedParams.advancedFilters[targetKey]
        : undefined;
    const targetQueryValue = parsedParams.searchInput || legacyDirectValue || '';

    const usesProviderFieldValue =
      urlSearchByTarget?.source === 'provider-field' &&
      urlSearchByTarget.field !== undefined &&
      urlSearchByTarget.field.type !== 'TextSearchField';

    if (usesProviderFieldValue && urlSearchByTarget?.field && targetQueryValue) {
      setSearchFieldValue(urlSearchByTarget.field.key, targetQueryValue);
    } else if (targetQueryValue) {
      setSearchInput(targetQueryValue);
    }

    const urlFieldValues =
      urlSearchByTarget?.source === 'provider-field' && urlSearchByTarget.field && targetQueryValue
        ? { [urlSearchByTarget.field.key]: targetQueryValue }
        : undefined;

    // Manual mode opens the release browser off an explicit submit - it has no results
    // list to bootstrap, so a deep link fills the input and stops there.
    if (urlSearchByTarget?.source === 'manual') {
      return;
    }

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
      searchInput: nextQueryTarget === 'general' ? targetQueryValue : '',
      showAdvanced: true,
      advancedFilters: {
        ...mergedFilters,
        isbn: nextQueryTarget === 'isbn' ? targetQueryValue : '',
        author: nextQueryTarget === 'author' ? targetQueryValue : '',
        title: nextQueryTarget === 'title' ? targetQueryValue : '',
      },
      bookLanguages,
      defaultLanguage: defaultLanguageCodes,
      searchMode: parsedSearchMode,
    });

    runSearchWithPolicyRefresh({
      query,
      contentTypeOverride: urlContentTypeOverride,
      searchModeOverride: parsedSearchMode,
      fieldValues: urlFieldValues,
    });
  });

  return null;
};
