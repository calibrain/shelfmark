import type { Dispatch, SetStateAction } from 'react';

import { useMountEffect } from '@/hooks/useMountEffect';
import type { AppConfig, AdvancedFilterState, ContentType, SortOption } from '@/types';
import { buildSearchQuery } from '@/utils/buildSearchQuery';
import { getEffectiveMetadataSort } from '@/utils/metadataSort';
import type { ParsedUrlSearch } from '@/utils/parseUrlSearchParams';

const ADVANCED_FILTER_VISIBILITY_KEYS = ['content', 'lang', 'formats'] as const;

interface UrlSearchBootstrapMountProps {
  parsedParams: ParsedUrlSearch;
  config: AppConfig;
  contentType: ContentType;
  combinedMode: boolean;
  combinedModeAllowed: boolean;
  advancedFilters: AdvancedFilterState;
  resolvedMetadataDefaultSort: string;
  resolvedMetadataSortOptions: SortOption[];
  setContentType: (value: ContentType) => void;
  setCombinedMode: (value: boolean) => void;
  setSearchInput: (value: string) => void;
  setAdvancedFilters: Dispatch<SetStateAction<AdvancedFilterState>>;
  setShowAdvanced: (value: boolean) => void;
  setActiveQueryTarget: (value: string) => void;
  runSearchWithPolicyRefresh: (opts: { query: string; contentTypeOverride?: ContentType }) => void;
  onComplete: () => void;
}

export const UrlSearchBootstrapMount = ({
  parsedParams,
  config,
  contentType,
  combinedMode,
  combinedModeAllowed,
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

    const urlContentTypeOverride = parsedParams.contentType;
    const urlForcesCombined = parsedParams.combinedMode === true && combinedModeAllowed;

    if (urlContentTypeOverride && urlContentTypeOverride !== contentType) {
      setContentType(urlContentTypeOverride);
    }

    if (urlForcesCombined && !combinedMode) {
      setCombinedMode(true);
    } else if (urlContentTypeOverride && combinedMode) {
      setCombinedMode(false);
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

    const nextQueryTarget = 'general';
    setActiveQueryTarget(nextQueryTarget);

    const resolvedUrlMetadataSort = getEffectiveMetadataSort({
      currentSort:
        typeof parsedParams.advancedFilters.sort === 'string'
          ? parsedParams.advancedFilters.sort
          : '',
      defaultSort: resolvedMetadataDefaultSort,
      sortOptions: resolvedMetadataSortOptions,
    });

    if (Object.keys(parsedParams.advancedFilters).length > 0) {
      setAdvancedFilters((prev) => ({
        ...prev,
        ...parsedParams.advancedFilters,
        ...(resolvedUrlMetadataSort ? { sort: resolvedUrlMetadataSort } : {}),
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
      ...(resolvedUrlMetadataSort ? { sort: resolvedUrlMetadataSort } : {}),
    };

    const query = buildSearchQuery({
      searchInput: parsedParams.searchInput,
      showAdvanced: true,
      advancedFilters: {
        ...mergedFilters,
        isbn: '',
        author: '',
        title: '',
      },
      bookLanguages,
      defaultLanguage: defaultLanguageCodes,
    });

    runSearchWithPolicyRefresh({
      query,
      contentTypeOverride: urlContentTypeOverride,
    });
  });

  return null;
};
