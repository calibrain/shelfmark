import { useCallback, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react';

import { useSocket } from '../../contexts/SocketContext';
import { getReleaseSources, getReleases } from '../../services/api';
import type {
  Book,
  ContentType,
  Language,
  ReleaseSource,
  ReleasesResponse,
  SearchStatusData,
} from '../../types';
import {
  LANGUAGE_OPTION_DEFAULT,
  getReleaseSearchLanguageParams,
} from '../../utils/languageFilters';
import {
  getCachedReleases,
  invalidateCachedReleases,
  setCachedReleases,
} from '../../utils/releaseCache';
import { useMountEffect } from '../useMountEffect';

export interface ReleaseModalTabInfo {
  name: string;
  displayName: string;
  enabled: boolean;
}

interface UseReleaseSearchSessionOptions {
  book: Book;
  contentType: ContentType;
  defaultReleaseSource?: string;
  defaultAudiobookReleaseSource?: string;
  defaultShowManualQuery?: boolean;
  bookLanguages: Language[];
  defaultLanguages: string[];
}

interface UseReleaseSearchSessionReturn {
  availableSources: ReleaseSource[];
  sourcesLoading: boolean;
  sourcesError: string | null;
  activeTab: string;
  setActiveTab: (tabName: string) => void;
  allTabs: ReleaseModalTabInfo[];
  releasesBySource: Record<string, ReleasesResponse | null>;
  loadingBySource: Record<string, boolean>;
  errorBySource: Record<string, string | null>;
  expandedBySource: Record<string, boolean>;
  searchStatus: SearchStatusData | null;
  formatFilter: string;
  setFormatFilter: Dispatch<SetStateAction<string>>;
  languageFilter: string[];
  setLanguageFilter: Dispatch<SetStateAction<string[]>>;
  indexerFilter: string[];
  setIndexerFilter: Dispatch<SetStateAction<string[]>>;
  manualQuery: string;
  setManualQuery: Dispatch<SetStateAction<string>>;
  showManualQuery: boolean;
  toggleManualQuery: () => void;
  applyCurrentFilters: () => void;
  runManualSearch: () => void;
  expandSearch: () => Promise<void>;
  isIndexerFilterInitialized: (tabName: string) => boolean;
}

const DEFAULT_EXPANDED_STATUS_DELAY_MS = 1500;

function getDefaultManualQuery(book: Book): string {
  const baseTitle = book.search_title || book.title || '';
  const baseAuthor = book.search_author || book.author || '';
  return `${baseTitle} ${baseAuthor}`.trim();
}

function buildReleaseTabs(
  availableSources: ReleaseSource[],
  providerName: string | undefined,
  contentType: ContentType,
  preferredDefaultReleaseSource: string | undefined,
): ReleaseModalTabInfo[] {
  type TabCandidate = { name: string; displayName: string; enabled: boolean };

  const enabledTabs: TabCandidate[] = [];
  const providerContextSourceName =
    availableSources.find(
      (source) => source.name === providerName && source.browse_results_are_releases,
    )?.name || null;

  availableSources.forEach((source) => {
    if (providerContextSourceName && source.name !== providerContextSourceName) {
      return;
    }

    const allowDisabledProviderContextTab = providerContextSourceName === source.name;
    if (!source.enabled && !allowDisabledProviderContextTab) {
      return;
    }

    const supportedTypes = source.supported_content_types || ['ebook', 'audiobook'];
    if (!supportedTypes.includes(contentType)) {
      return;
    }

    enabledTabs.push({ name: source.name, displayName: source.display_name, enabled: true });
  });

  if (preferredDefaultReleaseSource) {
    enabledTabs.sort((a, b) => {
      if (a.name === preferredDefaultReleaseSource) return -1;
      if (b.name === preferredDefaultReleaseSource) return 1;
      return 0;
    });
  }

  return enabledTabs;
}

export function useReleaseSearchSession(
  sessionOptions: UseReleaseSearchSessionOptions,
): UseReleaseSearchSessionReturn {
  const {
    book,
    contentType,
    defaultReleaseSource,
    defaultAudiobookReleaseSource,
    defaultShowManualQuery = false,
    bookLanguages,
    defaultLanguages,
  } = sessionOptions;
  const { socket } = useSocket();

  const preferredDefaultReleaseSource = useMemo(() => {
    if (contentType === 'audiobook') {
      return defaultAudiobookReleaseSource || defaultReleaseSource;
    }
    return defaultReleaseSource;
  }, [contentType, defaultAudiobookReleaseSource, defaultReleaseSource]);

  const defaultManualQuery = useMemo(() => getDefaultManualQuery(book), [book]);

  const [availableSources, setAvailableSources] = useState<ReleaseSource[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  const [activeTab, setActiveTabState] = useState(preferredDefaultReleaseSource || '');
  const [releasesBySource, setReleasesBySource] = useState<Record<string, ReleasesResponse | null>>(
    {},
  );
  const [loadingBySource, setLoadingBySource] = useState<Record<string, boolean>>({});
  const [errorBySource, setErrorBySource] = useState<Record<string, string | null>>({});
  const [expandedBySource, setExpandedBySource] = useState<Record<string, boolean>>({});
  const [searchStatus, setSearchStatus] = useState<SearchStatusData | null>(null);

  const [formatFilter, setFormatFilter] = useState('');
  const [languageFilter, setLanguageFilter] = useState([LANGUAGE_OPTION_DEFAULT]);
  const [indexerFilter, setIndexerFilter] = useState<string[]>([]);
  const [manualQuery, setManualQuery] = useState(defaultShowManualQuery ? defaultManualQuery : '');
  const [showManualQuery, setShowManualQuery] = useState(defaultShowManualQuery);

  const indexerFilterInitializedRef = useRef(new Set<string>());
  const initialActiveTabRef = useRef(activeTab);
  const activeTabRef = useRef(activeTab);
  const lastStatusTimeRef = useRef(0);
  const pendingStatusRef = useRef<SearchStatusData | null>(null);
  const statusTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  activeTabRef.current = activeTab;

  const allTabs = useMemo(() => {
    return buildReleaseTabs(
      availableSources,
      book.provider,
      contentType,
      preferredDefaultReleaseSource,
    );
  }, [availableSources, book.provider, contentType, preferredDefaultReleaseSource]);

  const clearSearchStatusForTab = useCallback((tabName: string) => {
    setSearchStatus((current) => (current?.source === tabName ? null : current));
  }, []);

  const isIndexerFilterInitialized = useCallback(
    (tabName: string) => indexerFilterInitializedRef.current.has(tabName),
    [],
  );

  const initializeIndexerFilterForTab = useCallback(
    (tabName: string, response: ReleasesResponse | null | undefined) => {
      if (!response?.column_config) {
        return;
      }

      if (indexerFilterInitializedRef.current.has(tabName)) {
        return;
      }

      const defaultIndexers = response.column_config.default_indexers;
      if (defaultIndexers && defaultIndexers.length > 0) {
        setIndexerFilter(defaultIndexers);
      }
      indexerFilterInitializedRef.current.add(tabName);
    },
    [],
  );

  const fetchReleaseResults = useCallback(
    async (
      tabName: string,
      options: {
        expandSearch?: boolean;
        useFilters?: boolean;
        force?: boolean;
        supportsIndexerFilter?: boolean;
        manualQueryOverride?: string;
      } = {},
    ): Promise<void> => {
      const {
        expandSearch = false,
        useFilters = false,
        force = false,
        supportsIndexerFilter = false,
        manualQueryOverride,
      } = options;

      if (!book.provider || !book.provider_id || !tabName) {
        return;
      }

      if (!force) {
        if (
          releasesBySource[tabName] !== undefined ||
          loadingBySource[tabName] ||
          errorBySource[tabName]
        ) {
          return;
        }
      }

      const provider = book.provider;
      const bookId = book.provider_id;
      const currentManualQuery = (manualQueryOverride ?? manualQuery).trim() || undefined;
      const languagesParam = useFilters
        ? getReleaseSearchLanguageParams(languageFilter, bookLanguages, defaultLanguages)
        : undefined;
      const indexersParam =
        useFilters && supportsIndexerFilter && indexerFilter.length > 0 ? indexerFilter : undefined;

      if (!expandSearch) {
        const cached = getCachedReleases(provider, bookId, tabName, contentType);
        if (cached) {
          setReleasesBySource((prev) => ({ ...prev, [tabName]: cached }));
          initializeIndexerFilterForTab(tabName, cached);
          clearSearchStatusForTab(tabName);
          return;
        }
      }

      setLoadingBySource((prev) => ({ ...prev, [tabName]: true }));
      setErrorBySource((prev) => ({ ...prev, [tabName]: null }));

      try {
        const response = await getReleases(
          provider,
          bookId,
          tabName,
          book.title,
          book.author,
          expandSearch || undefined,
          languagesParam,
          contentType,
          currentManualQuery,
          indexersParam,
        );

        if (expandSearch) {
          setReleasesBySource((prev) => {
            const existing = prev[tabName];
            if (!existing) {
              return { ...prev, [tabName]: response };
            }

            const seenIds = new Set(existing.releases.map((release) => release.source_id));
            const mergedReleases = response.releases.filter(
              (release) => !seenIds.has(release.source_id),
            );

            return {
              ...prev,
              [tabName]: {
                ...existing,
                releases: [...existing.releases, ...mergedReleases],
              },
            };
          });
        } else {
          setCachedReleases(provider, bookId, tabName, contentType, response);
          setReleasesBySource((prev) => ({ ...prev, [tabName]: response }));
        }

        initializeIndexerFilterForTab(tabName, response);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to fetch releases';
        setErrorBySource((prev) => ({ ...prev, [tabName]: message }));
      } finally {
        setLoadingBySource((prev) => ({ ...prev, [tabName]: false }));
        clearSearchStatusForTab(tabName);
      }
    },
    [
      book.author,
      book.provider,
      book.provider_id,
      book.title,
      bookLanguages,
      clearSearchStatusForTab,
      contentType,
      defaultLanguages,
      errorBySource,
      indexerFilter,
      initializeIndexerFilterForTab,
      languageFilter,
      loadingBySource,
      manualQuery,
      releasesBySource,
    ],
  );
  useMountEffect(() => {
    if (!book) {
      return undefined;
    }

    let cancelled = false;

    const fetchSources = async () => {
      try {
        setSourcesLoading(true);
        setSourcesError(null);
        const sources = await getReleaseSources();
        if (cancelled) {
          return;
        }

        setAvailableSources(sources);

        const tabs = buildReleaseTabs(
          sources,
          book.provider,
          contentType,
          preferredDefaultReleaseSource,
        );
        const initialActiveTab = initialActiveTabRef.current;
        const nextActiveTab = tabs.some((tab) => tab.name === initialActiveTab)
          ? initialActiveTab
          : (tabs[0]?.name ?? '');

        setActiveTabState(nextActiveTab);

        if (nextActiveTab) {
          void fetchReleaseResults(nextActiveTab, { force: false });
        }
      } catch (err) {
        console.error('Failed to fetch release sources:', err);
        if (!cancelled) {
          setAvailableSources([]);
          setSourcesError(err instanceof Error ? err.message : 'Failed to load release sources');
        }
      } finally {
        if (!cancelled) {
          setSourcesLoading(false);
        }
      }
    };

    void fetchSources();
    return () => {
      cancelled = true;
    };
  });

  useMountEffect(() => {
    if (!book || !socket) {
      return undefined;
    }

    const handleSearchStatus = (data: SearchStatusData) => {
      if (data.source !== activeTabRef.current) {
        return;
      }

      const now = Date.now();
      const elapsed = now - lastStatusTimeRef.current;

      if (elapsed >= DEFAULT_EXPANDED_STATUS_DELAY_MS) {
        setSearchStatus(data);
        lastStatusTimeRef.current = now;
        pendingStatusRef.current = null;
        return;
      }

      pendingStatusRef.current = data;

      if (statusTimeoutRef.current) {
        clearTimeout(statusTimeoutRef.current);
      }

      statusTimeoutRef.current = setTimeout(() => {
        if (pendingStatusRef.current) {
          setSearchStatus(pendingStatusRef.current);
          lastStatusTimeRef.current = Date.now();
          pendingStatusRef.current = null;
        }
      }, DEFAULT_EXPANDED_STATUS_DELAY_MS - elapsed);
    };

    socket.on('search_status', handleSearchStatus);

    return () => {
      socket.off('search_status', handleSearchStatus);
      if (statusTimeoutRef.current) {
        clearTimeout(statusTimeoutRef.current);
        statusTimeoutRef.current = null;
      }
      pendingStatusRef.current = null;
    };
  });

  const setActiveTab = useCallback(
    (tabName: string) => {
      setActiveTabState(tabName);

      if (!tabName) {
        return;
      }

      if (
        releasesBySource[tabName] === undefined &&
        !loadingBySource[tabName] &&
        !errorBySource[tabName]
      ) {
        void fetchReleaseResults(tabName, { force: false });
      }
    },
    [errorBySource, fetchReleaseResults, loadingBySource, releasesBySource],
  );

  const toggleManualQuery = useCallback(() => {
    setShowManualQuery((prev) => {
      const next = !prev;
      if (next && !manualQuery.trim()) {
        setManualQuery(defaultManualQuery);
      }
      return next;
    });
  }, [defaultManualQuery, manualQuery]);

  const applyCurrentFilters = useCallback(() => {
    if (!book.provider || !book.provider_id || !activeTab) {
      return;
    }

    const supportsIndexerFilter =
      releasesBySource[activeTab]?.column_config?.supported_filters?.includes('indexer') ?? false;

    invalidateCachedReleases(book.provider, book.provider_id, activeTab, contentType);
    setExpandedBySource((prev) => {
      const next = { ...prev };
      delete next[activeTab];
      return next;
    });
    setErrorBySource((prev) => {
      const next = { ...prev };
      delete next[activeTab];
      return next;
    });
    setReleasesBySource((prev) => {
      const next = { ...prev };
      delete next[activeTab];
      return next;
    });

    void fetchReleaseResults(activeTab, {
      force: true,
      useFilters: true,
      supportsIndexerFilter,
    });
  }, [
    activeTab,
    book.provider,
    book.provider_id,
    contentType,
    fetchReleaseResults,
    releasesBySource,
  ]);

  const runManualSearch = useCallback(() => {
    if (!book.provider || !book.provider_id || !activeTab) {
      return;
    }

    const manualSearchQuery = manualQuery.trim();
    if (!manualSearchQuery) {
      return;
    }

    for (const tab of allTabs) {
      invalidateCachedReleases(book.provider, book.provider_id, tab.name, contentType);
    }

    setExpandedBySource({});
    setErrorBySource({});
    setReleasesBySource({});

    void fetchReleaseResults(activeTab, {
      force: true,
      manualQueryOverride: manualSearchQuery,
    });
  }, [
    activeTab,
    allTabs,
    book.provider,
    book.provider_id,
    contentType,
    fetchReleaseResults,
    manualQuery,
  ]);

  const expandSearch = useCallback(async (): Promise<void> => {
    if (!book.provider || !book.provider_id || !activeTab) {
      return;
    }

    const supportsIndexerFilter =
      releasesBySource[activeTab]?.column_config?.supported_filters?.includes('indexer') ?? false;

    setLoadingBySource((prev) => ({ ...prev, [activeTab]: true }));
    setExpandedBySource((prev) => ({ ...prev, [activeTab]: true }));

    await fetchReleaseResults(activeTab, {
      force: true,
      expandSearch: true,
      useFilters: true,
      supportsIndexerFilter,
    });
  }, [activeTab, book.provider, book.provider_id, fetchReleaseResults, releasesBySource]);

  return {
    availableSources,
    sourcesLoading,
    sourcesError,
    activeTab,
    setActiveTab,
    allTabs,
    releasesBySource,
    loadingBySource,
    errorBySource,
    expandedBySource,
    searchStatus,
    formatFilter,
    setFormatFilter,
    languageFilter,
    setLanguageFilter,
    indexerFilter,
    setIndexerFilter,
    manualQuery,
    setManualQuery,
    showManualQuery,
    toggleManualQuery,
    applyCurrentFilters,
    runManualSearch,
    expandSearch,
    isIndexerFilterInitialized,
  };
}
