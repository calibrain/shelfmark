import { SortOption } from '../types';

const FALLBACK_SORT = 'relevance';

export const getEffectiveMetadataSort = ({
  currentSort,
  defaultSort,
  sortOptions,
}: {
  currentSort?: string;
  defaultSort?: string;
  sortOptions?: SortOption[];
}): string => {
  const supportedSorts = sortOptions && sortOptions.length > 0
    ? sortOptions.map((option) => option.value)
    : [FALLBACK_SORT];

  if (currentSort && supportedSorts.includes(currentSort)) {
    return currentSort;
  }

  if (defaultSort && supportedSorts.includes(defaultSort)) {
    return defaultSort;
  }

  return supportedSorts[0] || FALLBACK_SORT;
};
