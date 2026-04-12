import { useEffect, useState } from 'react';

import type { SortOption } from '../../types';
import { loadDynamicFieldOptions } from '../../utils/dynamicFieldOptions';

interface UseSearchBarDynamicOptionsResult {
  dynamicOptions: SortOption[];
  isDynamicLoading: boolean;
}

export const useSearchBarDynamicOptions = (
  dynamicEndpoint: string | null,
): UseSearchBarDynamicOptionsResult => {
  const [dynamicOptions, setDynamicOptions] = useState<SortOption[]>([]);
  const [isDynamicLoading, setIsDynamicLoading] = useState(false);

  useEffect(() => {
    if (!dynamicEndpoint) {
      setDynamicOptions([]);
      setIsDynamicLoading(false);
      return undefined;
    }

    let cancelled = false;
    setIsDynamicLoading(true);

    loadDynamicFieldOptions(dynamicEndpoint)
      .then((loaded) => {
        if (cancelled) return;
        setDynamicOptions(
          loaded.map((option) => ({
            value: option.value,
            label: option.label,
            group: option.group,
          })),
        );
        setIsDynamicLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setDynamicOptions([]);
        setIsDynamicLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [dynamicEndpoint]);

  return {
    dynamicOptions,
    isDynamicLoading,
  };
};
