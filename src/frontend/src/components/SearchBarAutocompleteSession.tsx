import { startTransition } from 'react';

import { useMountEffect } from '@/hooks/useMountEffect';
import type { DynamicFieldOption } from '@/services/api';
import { fetchFieldOptions } from '@/services/api';

const autocompleteOptionsCache = new Map<string, DynamicFieldOption[]>();
const AUTOCOMPLETE_CACHE_MAX = 100;

const cacheAutocompleteOptions = (cacheKey: string, options: DynamicFieldOption[]): void => {
  if (autocompleteOptionsCache.size >= AUTOCOMPLETE_CACHE_MAX) {
    const oldest = autocompleteOptionsCache.keys().next().value;
    if (oldest !== undefined) {
      autocompleteOptionsCache.delete(oldest);
    }
  }
  autocompleteOptionsCache.set(cacheKey, options);
};

interface SearchBarAutocompleteSessionProps {
  autocompleteEndpoint: string;
  query: string;
  minQueryLength: number;
  onLoading: (requestKey: string) => void;
  onResolved: (requestKey: string, options: DynamicFieldOption[]) => void;
}

export const SearchBarAutocompleteSession = ({
  autocompleteEndpoint,
  query,
  minQueryLength,
  onLoading,
  onResolved,
}: SearchBarAutocompleteSessionProps) => {
  useMountEffect(() => {
    const normalizedQuery = query.trim();
    if (normalizedQuery.length < minQueryLength) {
      return undefined;
    }

    const requestKey = `${autocompleteEndpoint}::${normalizedQuery.toLowerCase()}`;
    const cached = autocompleteOptionsCache.get(requestKey);
    if (cached) {
      onResolved(requestKey, cached);
      return undefined;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      if (cancelled) {
        return;
      }

      onLoading(requestKey);
      void fetchFieldOptions(autocompleteEndpoint, normalizedQuery)
        .then((loaded) => {
          if (cancelled) {
            return;
          }

          cacheAutocompleteOptions(requestKey, loaded);
          startTransition(() => {
            onResolved(requestKey, loaded);
          });
        })
        .catch(() => {
          if (cancelled) {
            return;
          }

          startTransition(() => {
            onResolved(requestKey, []);
          });
        });
    }, 260);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  });

  return null;
};
