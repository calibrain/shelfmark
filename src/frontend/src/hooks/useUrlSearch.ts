import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

import type { ParsedUrlSearch } from '../utils/parseUrlSearchParams';
import { parseUrlSearchParams } from '../utils/parseUrlSearchParams';

interface UseUrlSearchOptions {
  /** Only process URL params after auth check and config are loaded */
  enabled: boolean;
}

interface UseUrlSearchReturn {
  /** Parsed URL parameters, or null if none found */
  parsedParams: ParsedUrlSearch | null;
  /** Whether URL has been processed (regardless of whether params existed) */
  wasProcessed: boolean;
}

/**
 * Hook to parse URL search parameters on initial page load.
 *
 * This is a read-only operation - URL params are parsed once when enabled,
 * and the URL is not updated when users perform searches.
 *
 * @example
 * // In App.tsx:
 * const { parsedParams, wasProcessed } = useUrlSearch({
 *   enabled: isAuthenticated && config !== null,
 * });
 *
 * // React to the parsed params once processing is complete.
 * if (wasProcessed && parsedParams?.hasSearchParams) {
 *   // Trigger search with parsed params
 * }
 */
export function useUrlSearch({ enabled }: UseUrlSearchOptions): UseUrlSearchReturn {
  const [searchParams] = useSearchParams();
  const parsedParams = useMemo(() => {
    if (!enabled) {
      return null;
    }

    const parsed = parseUrlSearchParams(searchParams);
    return parsed.hasSearchParams || parsed.contentType ? parsed : null;
  }, [enabled, searchParams]);

  return {
    parsedParams,
    wasProcessed: enabled,
  };
}
