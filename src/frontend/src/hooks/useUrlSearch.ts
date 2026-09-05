import { useMemo } from 'react';

import type { ParsedUrlSearch } from '../utils/parseUrlSearchParams';
import { parseUrlSearchParams } from '../utils/parseUrlSearchParams';
import { useLatestCallback } from './useLatestCallback';
import { useDependencyEffect, useMountEffect } from './useMountEffect';

interface UseUrlSearchOptions {
  /** Only process URL params after auth check and config are loaded */
  enabled: boolean;
  /**
   * Bump to re-read the URL - used when the hash changes underneath us
   * (someone pastes a shared link into an already-open tab).
   */
  nonce?: number;
}

interface UseUrlSearchReturn {
  /** Parsed URL parameters, or null if none found */
  parsedParams: ParsedUrlSearch | null;
  /** Whether URL has been processed (regardless of whether params existed) */
  wasProcessed: boolean;
}

/** Debounce for the write side: searchInput changes on every keystroke, and Safari
 *  throws SecurityError past ~100 replaceState calls per 30s. */
const HASH_SYNC_DEBOUNCE_MS = 300;

/**
 * Last hash this module wrote. Lets the hashchange listener tell "the user pasted a
 * new link" apart from "our own sync effect just ran".
 */
let lastWrittenHash: string | null = null;

const stripHash = (value: string): string => (value.startsWith('#') ? value.slice(1) : value);

const readUrlSearchParams = (): { params: URLSearchParams; fromQueryString: boolean } => {
  const hash = stripHash(window.location.hash);
  if (hash) {
    return { params: new URLSearchParams(hash), fromQueryString: false };
  }
  const search = window.location.search.startsWith('?')
    ? window.location.search.slice(1)
    : window.location.search;
  return { params: new URLSearchParams(search), fromQueryString: Boolean(search) };
};

/**
 * Hook to parse the URL on initial page load.
 *
 * Search config lives in a hash fragment (e.g. `#q=dune&search_by=manual`)
 * rather than query params, so it stays browser-side only and never looks
 * like a server-processed query string.
 *
 * Query-string links (`?q=dune`) shipped before the hash and are still honoured
 * when the hash is empty: they're read once and rewritten to `#…` so the two
 * can't drift as the live sync below updates the URL.
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
export function useUrlSearch({ enabled, nonce = 0 }: UseUrlSearchOptions): UseUrlSearchReturn {
  const read = useMemo(() => {
    if (!enabled) {
      return null;
    }

    const { params, fromQueryString } = readUrlSearchParams();
    const parsed = parseUrlSearchParams(params);
    const hasAnything = Boolean(
      parsed.hasSearchParams || parsed.contentType || parsed.combinedMode || parsed.searchBy,
    );

    return { parsed: hasAnything ? parsed : null, fromQueryString, hasAnything };
    // Intentionally read once per enable/nonce - live updates come from useSyncUrlSearchHash.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, nonce]);

  // Migrate a legacy query-string link to the hash form once, so the live sync has a single
  // source of truth and the URL the user re-shares is the one the app keeps updating.
  const shouldMigrate = Boolean(read?.fromQueryString && read.hasAnything);
  useDependencyEffect(() => {
    if (!shouldMigrate) {
      return;
    }

    const nextHash = new URLSearchParams(window.location.search).toString();
    lastWrittenHash = nextHash;
    try {
      window.history.replaceState(
        window.history.state,
        '',
        `${window.location.pathname}#${nextHash}`,
      );
    } catch {
      // replaceState is rate-limited in Safari - the parsed params still apply
    }
  }, [shouldMigrate]);

  return {
    parsedParams: read?.parsed ?? null,
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
 * history entry per change. Debounced, because Safari throws SecurityError
 * past roughly 100 replaceState calls per 30 seconds.
 */
export function useSyncUrlSearchHash({ enabled, hash }: UseSyncUrlSearchHashOptions): void {
  useDependencyEffect(() => {
    // Debounced, so a burst of keystrokes collapses into one history write.
    const timer = enabled
      ? window.setTimeout(() => {
          if (stripHash(window.location.hash) === hash) {
            lastWrittenHash = hash;
            return;
          }

          const url = `${window.location.pathname}${window.location.search}${hash ? `#${hash}` : ''}`;
          lastWrittenHash = hash;
          try {
            window.history.replaceState(window.history.state, '', url);
          } catch {
            // Rate-limited (Safari) or a sandboxed frame - the UI state is still correct,
            // only the shareable URL lags behind.
          }
        }, HASH_SYNC_DEBOUNCE_MS)
      : undefined;

    return () => {
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [enabled, hash]);
}

/**
 * Calls `onExternalChange` when the hash changes to something this module didn't write -
 * i.e. someone pasted a shared link into an already-open tab, or used back/forward.
 */
export function useExternalHashChange(onExternalChange: () => void): void {
  // The listener outlives the Effect that registers it and fires from a DOM event,
  // which is outside useEffectEvent's contract - see useLatestCallback.
  const notify = useLatestCallback(onExternalChange);

  useMountEffect(() => {
    const handleHashChange = () => {
      if (stripHash(window.location.hash) === lastWrittenHash) {
        return;
      }
      notify();
    };

    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  });
}
