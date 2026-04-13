import type { SortOption } from '@/types';
import { loadDynamicFieldOptions } from '@/utils/dynamicFieldOptions';

import { useMountEffect } from '../hooks/useMountEffect';

interface SearchBarDynamicOptionsSessionProps {
  dynamicEndpoint: string;
  onResolved: (options: SortOption[]) => void;
}

export const SearchBarDynamicOptionsSession = ({
  dynamicEndpoint,
  onResolved,
}: SearchBarDynamicOptionsSessionProps) => {
  useMountEffect(() => {
    let cancelled = false;

    void loadDynamicFieldOptions(dynamicEndpoint)
      .then((loaded) => {
        if (cancelled) {
          return;
        }

        onResolved(
          loaded.map((option) => ({
            value: option.value,
            label: option.label,
            group: option.group,
          })),
        );
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        onResolved([]);
      });

    return () => {
      cancelled = true;
    };
  });

  return null;
};
