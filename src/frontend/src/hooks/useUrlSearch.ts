import { useMemo } from 'react';

import { useDependencyEffect } from './useMountEffect';
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

const readHashSearchParams = (): URLSearchParams => {
  const hash = window.location.hash;
  return new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
};

/**
 * Hook to parse the URL hash fragment on initial page load.
 *
 * Search config lives in a hash fragment (e.g. `#q=dune&search_by=manual`)
 * rather than query params, so it stays browser-side only and never looks
 * like a server-processed query string.
 *
 * This only reads the hash once when enabled - see useSyncUrlSearchHash for
 * the write side that keeps the hash live as the user searches.
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
  const parsedParams = useMemo(() => {
    if (!enabled) {
      return null;
    }

    const parsed = parseUrlSearchParams(readHashSearchParams());
    return parsed.hasSearchParams || parsed.contentType || parsed.combinedMode || parsed.searchBy
      ? parsed
      : null;
    // Intentionally read once on enable - live updates come from useSyncUrlSearchHash.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  return {
    parsedParams,
    wasProcessed: enabled,
  };
}

interface UseSyncUrlSearchHashOptions {
  /** Only write once URL params (if any) have been applied to search state */
  enabled: boolean;
  /** Hash fragment (without leading `#`) that should reflect current search state */
  hash: string;
}

/**
 * Keeps the URL hash fragment in sync with the current search state.
 *
 * Uses history.replaceState (not pushState), so every keystroke or
 * Search By change updates the URL live without pushing a new browser
 * history entry per change.
 */
export function useSyncUrlSearchHash({ enabled, hash }: UseSyncUrlSearchHashOptions): void {
  useDependencyEffect(() => {
    if (!enabled) {
      return;
    }

    const currentHash = window.location.hash.startsWith('#')
      ? window.location.hash.slice(1)
      : window.location.hash;
    if (currentHash === hash) {
      return;
    }

    const url = `${window.location.pathname}${window.location.search}${hash ? `#${hash}` : ''}`;
    window.history.replaceState(window.history.state, '', url);
  }, [enabled, hash]);
}
