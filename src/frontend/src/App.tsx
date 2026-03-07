import { useState, useEffect, useCallback, useRef, useMemo, CSSProperties } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import {
  Book,
  Release,
  RequestRecord,
  StatusData,
  AppConfig,
  ContentType,
  ButtonStateInfo,
  RequestPolicyMode,
  CreateRequestPayload,
  ActingAsUserSelection,
  MetadataProviderSummary,
  MetadataSearchConfig,
  QueryTargetOption,
  SearchMode,
  isMetadataBook,
} from './types';
import {
  getSourceRecordInfo,
  getMetadataBookInfo,
  downloadRelease,
  cancelDownload,
  retryDownload,
  getConfig,
  getMetadataProviders,
  getMetadataSearchConfig,
  createRequest,
  isApiResponseError,
  updateSelfUser,
  setBookTargetState,
  type DownloadReleasePayload,
} from './services/api';
import { useToast } from './hooks/useToast';
import { useRealtimeStatus } from './hooks/useRealtimeStatus';
import { useAuth } from './hooks/useAuth';
import { useSearch } from './hooks/useSearch';
import { useUrlSearch } from './hooks/useUrlSearch';
import { useDownloadTracking } from './hooks/useDownloadTracking';
import { useRequestPolicy } from './hooks/useRequestPolicy';
import { resolveDefaultModeFromPolicy, resolveSourceModeFromPolicy } from './hooks/requestPolicyCore';
import { useRequests } from './hooks/useRequests';
import { useActivity } from './hooks/useActivity';
import { Header } from './components/Header';
import { SearchSection } from './components/SearchSection';
import { AdvancedFilters } from './components/AdvancedFilters';
import { ResultsSection } from './components/ResultsSection';
import { DetailsModal } from './components/DetailsModal';
import { ReleaseModal } from './components/ReleaseModal';
import { RequestConfirmationModal } from './components/RequestConfirmationModal';
import { OnBehalfConfirmationModal } from './components/OnBehalfConfirmationModal';
import { ToastContainer } from './components/ToastContainer';
import { Footer } from './components/Footer';
import { ActivitySidebar } from './components/activity';
import { LoginPage } from './pages/LoginPage';
import { SelfSettingsModal, SettingsModal } from './components/settings';
import { ConfigSetupBanner } from './components/ConfigSetupBanner';
import { OnboardingModal } from './components/OnboardingModal';
import { DEFAULT_LANGUAGES, DEFAULT_SUPPORTED_FORMATS } from './data/languages';
import { buildSearchQuery } from './utils/buildSearchQuery';
import { formatActingAsUserName } from './utils/actingAsUser';
import { withBasePath } from './utils/basePath';
import { getConfiguredMetadataProviderForContentType } from './utils/metadataProviders';
import { getEffectiveMetadataSort } from './utils/metadataSort';
import {
  applyDirectPolicyModeToButtonState,
  applyUniversalPolicyModeToButtonState,
} from './utils/requestPolicyUi';
import {
  buildDirectRequestPayload,
  buildReleaseDataFromDirectBook,
  buildMetadataBookRequestData,
  buildReleaseDataFromMetadataRelease,
  getBrowseSource,
  getRequestSuccessMessage,
  toContentType,
} from './utils/requestPayload';
import { bookFromRequestData } from './utils/requestFulfil';
import { emitBookTargetChange, onBookTargetChange } from './utils/bookTargetEvents';
import { bookSupportsTargets } from './utils/bookTargetLoader';
import { policyTrace } from './utils/policyTrace';
import { SearchModeProvider } from './contexts/SearchModeContext';
import { useSocket } from './contexts/SocketContext';
import { buildQueryTargets, getDefaultQueryTargetKey } from './utils/queryTargets';
import './styles.css';

const CONTENT_TYPE_STORAGE_KEY = 'preferred-content-type';

const getInitialContentType = (): ContentType => {
  try {
    const saved = localStorage.getItem(CONTENT_TYPE_STORAGE_KEY);
    if (saved === 'ebook' || saved === 'audiobook') {
      return saved;
    }
  } catch {
    // localStorage may be unavailable in private browsing
  }
  return 'ebook';
};

const POLICY_GUARD_ERROR_CODES = new Set(['policy_requires_request', 'policy_blocked']);
const isPolicyGuardError = (error: unknown): boolean => {
  return (
    isApiResponseError(error) &&
    error.status === 403 &&
    Boolean(error.code && POLICY_GUARD_ERROR_CODES.has(error.code))
  );
};

const asRequestPolicyMode = (value: unknown): RequestPolicyMode | null => {
  return value === 'download' || value === 'request_release' || value === 'request_book' || value === 'blocked'
    ? value
    : null;
};

const getPolicyGuardRequiredMode = (error: unknown): RequestPolicyMode | null => {
  if (!isPolicyGuardError(error) || !isApiResponseError(error)) {
    return null;
  }
  const explicitMode = asRequestPolicyMode(error.requiredMode);
  if (explicitMode) {
    return explicitMode;
  }
  if (error.code === 'policy_blocked') {
    return 'blocked';
  }
  return null;
};

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

type PendingOnBehalfDownload =
  | {
      type: 'book';
      book: Book;
      actingAsUser: ActingAsUserSelection;
    }
  | {
      type: 'release';
      book: Book;
      release: Release;
      releaseContentType: ContentType;
      actingAsUser: ActingAsUserSelection;
    };

function App() {
  const { toasts, showToast, removeToast } = useToast();
  const { socket } = useSocket();

  // Realtime status with WebSocket and polling fallback
  // Socket connection is managed by SocketProvider in main.tsx
  const {
    status: currentStatus,
    isUsingWebSocket,
    forceRefresh: fetchStatus
  } = useRealtimeStatus({
    pollInterval: 5000,
  });

  // Download tracking for universal mode
  const {
    bookToReleaseMap,
    trackRelease,
    markBookCompleted,
    clearTracking,
    getButtonState,
    getUniversalButtonState,
  } = useDownloadTracking(currentStatus);

  // Authentication state and handlers
  // Initialized first since search hook needs auth state
  const {
    isAuthenticated,
    authRequired,
    authChecked,
    isAdmin: authIsAdmin,
    authMode,
    username,
    displayName,
    oidcButtonLabel,
    hideLocalAuth,
    oidcAutoRedirect,
    loginError,
    isLoggingIn,
    setIsAuthenticated,
    refreshAuth,
    handleLogin,
    handleLogout,
  } = useAuth({
    showToast,
  });

  // Re-request status after auth is established so the server can re-scope socket room membership.
  useEffect(() => {
    if (!authChecked || !isAuthenticated) {
      return;
    }
    policyTrace('auth.status', { authChecked, isAuthenticated, isAdmin: authIsAdmin, username });
    void fetchStatus();
  }, [authChecked, isAuthenticated, authIsAdmin, username, fetchStatus]);

  // Content type state (ebook vs audiobook) - defined before useSearch since it's passed to it
  const [contentType, setContentType] = useState<ContentType>(() => getInitialContentType());

  useEffect(() => {
    try {
      localStorage.setItem(CONTENT_TYPE_STORAGE_KEY, contentType);
    } catch {
      // localStorage may be unavailable in private browsing
    }
  }, [contentType]);

  const {
    policy: requestPolicy,
    getDefaultMode,
    getSourceMode,
    requestsEnabled: requestsPolicyEnabled,
    allowNotes: allowRequestNotes,
    refresh: refreshRequestPolicy,
  } = useRequestPolicy({
    enabled: isAuthenticated,
    isAdmin: authIsAdmin,
  });

  const requestRoleIsAdmin = requestPolicy ? Boolean(requestPolicy.is_admin) : false;

  // Compute which content types this user is allowed to search for.
  // If a content type's default policy mode is 'blocked', hide it from the dropdown.
  const allowedContentTypes = useMemo((): ContentType[] => {
    // If policy not loaded yet or user is admin, allow everything
    if (!requestPolicy || requestRoleIsAdmin || !requestsPolicyEnabled) {
      return ['ebook', 'audiobook'];
    }
    const types: ContentType[] = [];
    if (getDefaultMode('ebook') !== 'blocked') types.push('ebook');
    if (getDefaultMode('audiobook') !== 'blocked') types.push('audiobook');
    // If both are blocked, still show both (user can see results, just can't download)
    return types.length > 0 ? types : ['ebook', 'audiobook'];
  }, [requestPolicy, requestRoleIsAdmin, requestsPolicyEnabled, getDefaultMode]);

  // Auto-switch content type if the current selection is blocked
  useEffect(() => {
    if (allowedContentTypes.length > 0 && !allowedContentTypes.includes(contentType)) {
      setContentType(allowedContentTypes[0]);
    }
  }, [allowedContentTypes, contentType]);

  const {
    isLoading: isRequestsLoading,
    cancelRequest: cancelUserRequest,
    fulfilRequest: fulfilSidebarRequest,
    rejectRequest: rejectSidebarRequest,
  } = useRequests({
    isAdmin: requestRoleIsAdmin,
    enabled: isAuthenticated,
  });

  const {
    activityStatus,
    requestItems,
    dismissedActivityKeys,
    historyItems,
    activityHistoryLoaded,
    pendingRequestCount,
    isActivitySnapshotLoading,
    activityHistoryLoading,
    activityHistoryHasMore,
    prefetchActivityHistory,
    refreshActivitySnapshot,
    resetActivity,
    handleActivityTabChange,
    handleActivityHistoryLoadMore,
    handleRequestDismiss,
    handleDownloadDismiss,
    handleClearCompleted,
    handleClearHistory,
  } = useActivity({
    isAuthenticated,
    isAdmin: requestRoleIsAdmin,
    showToast,
    socket,
  });

  const dismissedDownloadTaskIds = useMemo(() => {
    const result = new Set<string>();
    for (const key of dismissedActivityKeys) {
      if (typeof key !== 'string' || !key.startsWith('download:')) {
        continue;
      }
      const taskId = key.substring('download:'.length).trim();
      if (taskId) {
        result.add(taskId);
      }
    }
    return result;
  }, [dismissedActivityKeys]);

  const isDownloadTaskDismissed = useCallback((taskId: string) => {
    return dismissedDownloadTaskIds.has(taskId);
  }, [dismissedDownloadTaskIds]);

  const statusForButtonState = useMemo(() => {
    if (!currentStatus.complete || dismissedDownloadTaskIds.size === 0) {
      return currentStatus;
    }

    const filteredComplete = Object.fromEntries(
      Object.entries(currentStatus.complete).filter(([taskId]) => !dismissedDownloadTaskIds.has(taskId))
    ) as Record<string, Book>;

    if (Object.keys(filteredComplete).length === Object.keys(currentStatus.complete).length) {
      return currentStatus;
    }

    return {
      ...currentStatus,
      complete: filteredComplete,
    };
  }, [currentStatus, dismissedDownloadTaskIds]);

  // Use real-time buckets for active work and persisted activity snapshot
  // buckets for terminal history. Filter out dismissed items so the sidebar
  // counts stay consistent with the activity panel.
  const activitySidebarStatus = useMemo<StatusData>(() => {
    const filterDismissed = (
      bucket: Record<string, Book> | undefined
    ): Record<string, Book> | undefined => {
      if (!bucket || dismissedDownloadTaskIds.size === 0) return bucket;
      const filtered = Object.fromEntries(
        Object.entries(bucket).filter(([taskId]) => !dismissedDownloadTaskIds.has(taskId))
      ) as Record<string, Book>;
      return Object.keys(filtered).length > 0 ? filtered : undefined;
    };

    return {
      queued: currentStatus.queued,
      resolving: currentStatus.resolving,
      locating: currentStatus.locating,
      downloading: currentStatus.downloading,
      complete: filterDismissed(activityStatus.complete),
      error: filterDismissed(activityStatus.error),
      cancelled: filterDismissed(activityStatus.cancelled),
    };
  }, [activityStatus, currentStatus, dismissedDownloadTaskIds]);

  const showRequestsTab = useMemo(() => {
    if (requestRoleIsAdmin) {
      return true;
    }
    if (!isAuthenticated || !requestsPolicyEnabled) {
      return false;
    }
    if (!requestPolicy) {
      return false;
    }
    return !(
      requestPolicy.defaults.ebook === 'download' &&
      requestPolicy.defaults.audiobook === 'download'
    );
  }, [requestRoleIsAdmin, isAuthenticated, requestsPolicyEnabled, requestPolicy]);

  // Search state and handlers
  const {
    books,
    setBooks,
    isSearching,
    searchInput,
    setSearchInput,
    showAdvanced,
    setShowAdvanced,
    advancedFilters,
    setAdvancedFilters,
    updateAdvancedFilters,
    handleSearch,
    handleResetSearch,
    searchFieldValues,
    updateSearchFieldValue,
    searchFieldLabels,
    // Pagination (universal mode)
    hasMore,
    isLoadingMore,
    loadMore,
    totalFound,
    resultsSourceUrl,
  } = useSearch({
    showToast,
    setIsAuthenticated,
    authRequired,
    onSearchReset: clearTracking,
    contentType,
  });

  // When a book is removed from the Hardcover list currently being browsed, remove it from results
  const searchFieldValuesRef = useRef(searchFieldValues);
  searchFieldValuesRef.current = searchFieldValues;

  useEffect(() => {
    return onBookTargetChange((event) => {
      if (event.selected) return;
      const activeListValue = searchFieldValuesRef.current.hardcover_list;
      if (!activeListValue || String(activeListValue) !== event.target) return;
      setBooks((prev) => prev.filter((book) => book.provider_id !== event.bookId));
    });
  }, [setBooks]);

  const [pendingRequestPayload, setPendingRequestPayload] = useState<CreateRequestPayload | null>(null);
  const [actingAsUser, setActingAsUser] = useState<ActingAsUserSelection | null>(null);
  const [pendingOnBehalfDownload, setPendingOnBehalfDownload] = useState<PendingOnBehalfDownload | null>(null);
  const [fulfillingRequest, setFulfillingRequest] = useState<{
    requestId: number;
    book: Book;
    contentType: ContentType;
  } | null>(null);

  // Wire up logout callback to clear search state
  const handleLogoutWithCleanup = useCallback(async () => {
    await handleLogout();
    setBooks([]);
    clearTracking();
    setActiveQueryTarget('general');
    setPendingRequestPayload(null);
    setActingAsUser(null);
    setPendingOnBehalfDownload(null);
    setFulfillingRequest(null);
    resetActivity();
    setSettingsOpen(false);
    setSelfSettingsOpen(false);
  }, [handleLogout, setBooks, clearTracking, resetActivity]);

  useEffect(() => {
    if (isAuthenticated && authIsAdmin) {
      return;
    }
    setActingAsUser(null);
    setPendingOnBehalfDownload(null);
  }, [isAuthenticated, authIsAdmin]);

  // UI state
  const [selectedBook, setSelectedBook] = useState<Book | null>(null);
  const [releaseBook, setReleaseBook] = useState<Book | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [metadataProviders, setMetadataProviders] = useState<MetadataProviderSummary[]>([]);
  const [configuredMetadataProvider, setConfiguredMetadataProvider] = useState<string | null>(null);
  const [configuredAudiobookMetadataProvider, setConfiguredAudiobookMetadataProvider] = useState<string | null>(null);
  const [activeMetadataConfig, setActiveMetadataConfig] = useState<MetadataSearchConfig | null>(null);
  const [activeQueryTarget, setActiveQueryTarget] = useState<string>('general');
  const [activeResultsSort, setActiveResultsSort] = useState('');
  const [downloadsSidebarOpen, setDownloadsSidebarOpen] = useState(false);
  const [sidebarPinnedOpen, setSidebarPinnedOpen] = useState(false);
  const [headerHeight, setHeaderHeight] = useState(0);
  const headerObserverRef = useRef<ResizeObserver | null>(null);
  useEffect(() => {
    if (!downloadsSidebarOpen) {
      return;
    }
    prefetchActivityHistory();
  }, [downloadsSidebarOpen, prefetchActivityHistory]);

  const headerRef = useCallback((el: HTMLDivElement | null) => {
    if (headerObserverRef.current) {
      headerObserverRef.current.disconnect();
      headerObserverRef.current = null;
    }
    if (!el) return;
    setHeaderHeight(el.getBoundingClientRect().height);
    const observer = new ResizeObserver(() => {
      setHeaderHeight(el.getBoundingClientRect().height);
    });
    observer.observe(el);
    headerObserverRef.current = observer;
  }, []);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [selfSettingsOpen, setSelfSettingsOpen] = useState(false);
  const [configBannerOpen, setConfigBannerOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(false);

  // Expose debug function to trigger onboarding from browser console
  useEffect(() => {
    (window as unknown as { showOnboarding: () => void }).showOnboarding = () => setOnboardingOpen(true);
    return () => {
      delete (window as unknown as { showOnboarding?: () => void }).showOnboarding;
    };
  }, []);

  // URL-based search: parse URL params for automatic search on page load
  const urlSearchEnabled = isAuthenticated && config !== null;
  const { parsedParams, wasProcessed } = useUrlSearch({ enabled: urlSearchEnabled });
  const urlSearchExecutedRef = useRef(false);

  // Track previous status and search mode for change detection
  const prevStatusRef = useRef<StatusData>({});
  const prevSearchModeRef = useRef<string | undefined>(undefined);

  // Calculate status counts for header badges (memoized)
  const statusCounts = useMemo(() => {
    const dismissedKeySet = new Set(dismissedActivityKeys);
    const countVisibleDownloads = (
      bucket: Record<string, Book> | undefined,
      options: { filterDismissed: boolean }
    ): number => {
      const { filterDismissed } = options;
      if (!bucket) {
        return 0;
      }
      if (!filterDismissed) {
        return Object.keys(bucket).length;
      }
      return Object.keys(bucket).filter((taskId) => !dismissedKeySet.has(`download:${taskId}`)).length;
    };

    const ongoing = [
      activitySidebarStatus.queued,
      activitySidebarStatus.resolving,
      activitySidebarStatus.locating,
      activitySidebarStatus.downloading,
    ].reduce((sum, status) => sum + countVisibleDownloads(status, { filterDismissed: false }), 0);

    const completed = countVisibleDownloads(activitySidebarStatus.complete, { filterDismissed: true });
    const errored = countVisibleDownloads(activitySidebarStatus.error, { filterDismissed: true });
    const pendingVisibleRequests = requestItems.filter((item) => {
      const requestId = item.requestId;
      if (!requestId || item.requestRecord?.status !== 'pending') {
        return false;
      }
      return !dismissedKeySet.has(`request:${requestId}`);
    }).length;

    return {
      ongoing,
      completed,
      errored,
      pendingRequests: pendingVisibleRequests,
    };
  }, [activitySidebarStatus, dismissedActivityKeys, requestItems]);


  // Compute visibility states
  const hasResults = books.length > 0;
  const isInitialState = !hasResults;

  // Detect status changes and show notifications
  const detectChanges = useCallback((prev: StatusData, curr: StatusData) => {
    if (!prev || Object.keys(prev).length === 0) return;

    // Check for new items in queue
    const prevQueued = prev.queued || {};
    const currQueued = curr.queued || {};
    Object.keys(currQueued).forEach(bookId => {
      if (!prevQueued[bookId]) {
        const book = currQueued[bookId];
        showToast(`${book.title || 'Book'} added to queue`, 'info');
        // Auto-open downloads sidebar if enabled
        if (config?.auto_open_downloads_sidebar !== false) {
          setDownloadsSidebarOpen(true);
        }
      }
    });

    // Check for items that started downloading
    const prevDownloading = prev.downloading || {};
    const currDownloading = curr.downloading || {};
    Object.keys(currDownloading).forEach(bookId => {
      if (!prevDownloading[bookId]) {
        const book = currDownloading[bookId];
        showToast(`${book.title || 'Book'} started downloading`, 'info');
      }
    });

    // Check for completed items
    const prevDownloadingIds = new Set(Object.keys(prevDownloading));
    const prevResolvingIds = new Set(Object.keys(prev.resolving || {}));
    const prevQueuedIds = new Set(Object.keys(prevQueued));
    const currComplete = curr.complete || {};

    Object.keys(currComplete).forEach(bookId => {
      if (prevDownloadingIds.has(bookId) || prevQueuedIds.has(bookId)) {
        const book = currComplete[bookId];
        showToast(`${book.title || 'Book'} completed`, 'success');

        // Auto-download to browser if enabled
        if (config?.download_to_browser && book.download_path) {
          const link = document.createElement('a');
          link.href = withBasePath(`/api/localdownload?id=${encodeURIComponent(bookId)}`);
          link.download = '';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
        }

        // Track completed release IDs in session state for universal mode
        Object.entries(bookToReleaseMap).forEach(([metadataBookId, releaseIds]) => {
          if (releaseIds.includes(bookId)) {
            markBookCompleted(metadataBookId);
          }
        });
      }
    });

    // Check for failed items
    const currError = curr.error || {};
    Object.keys(currError).forEach(bookId => {
      if (prevDownloadingIds.has(bookId) || prevResolvingIds.has(bookId) || prevQueuedIds.has(bookId)) {
        const book = currError[bookId];
        const errorMsg = book.status_message || 'Download failed';
        showToast(`${book.title || 'Book'}: ${errorMsg}`, 'error');
      }
    });
  }, [showToast, bookToReleaseMap, markBookCompleted, config]);

  // Detect status changes when currentStatus updates
  useEffect(() => {
    if (prevStatusRef.current && Object.keys(prevStatusRef.current).length > 0) {
      detectChanges(prevStatusRef.current, currentStatus);
    }
    prevStatusRef.current = currentStatus;
  }, [currentStatus, detectChanges]);

  // Load config function
  const loadConfig = useCallback(async (mode: 'initial' | 'settings-saved' = 'initial') => {
    try {
      const [cfg, metadataProviderState] = await Promise.all([
        getConfig(),
        getMetadataProviders(),
      ]);
      const activeConfiguredProvider = getConfiguredMetadataProviderForContentType({
        contentType,
        configuredMetadataProvider: metadataProviderState.configured_provider,
        configuredAudiobookMetadataProvider: metadataProviderState.configured_provider_audiobook,
      });
      let nextMetadataConfig: MetadataSearchConfig | null = null;

      if (cfg.search_mode === 'universal') {
        try {
          nextMetadataConfig = await getMetadataSearchConfig(
            contentType,
            activeConfiguredProvider ?? undefined,
          );
        } catch (metadataConfigError) {
          console.error('Failed to load metadata search config during config sync:', metadataConfigError);
        }
      }

      const resolvedMetadataDefaultSort = getEffectiveMetadataSort({
        currentSort: '',
        defaultSort: nextMetadataConfig?.default_sort || cfg.metadata_default_sort || 'relevance',
        sortOptions: nextMetadataConfig?.sort_options ?? cfg.metadata_sort_options,
      });

      // Check if search mode changed (only on settings save)
      if (mode === 'settings-saved' && prevSearchModeRef.current !== cfg.search_mode) {
        setBooks([]);
        setSelectedBook(null);
        clearTracking();
      }

      prevSearchModeRef.current = cfg.search_mode;
      setConfig({
        ...cfg,
        metadata_default_sort: resolvedMetadataDefaultSort,
        metadata_sort_options: nextMetadataConfig?.sort_options ?? cfg.metadata_sort_options,
      });
      setMetadataProviders(metadataProviderState.providers);
      setConfiguredMetadataProvider(metadataProviderState.configured_provider);
      setConfiguredAudiobookMetadataProvider(metadataProviderState.configured_provider_audiobook);
      setActiveMetadataConfig(nextMetadataConfig);

      // Show onboarding modal on first run (settings enabled but not completed yet)
      if (mode === 'initial' && cfg.settings_enabled && !cfg.onboarding_complete) {
        setOnboardingOpen(true);
      }

      // Determine the default sort based on search mode
      const defaultSort = cfg.search_mode === 'universal'
        ? resolvedMetadataDefaultSort
        : (cfg.default_sort || 'relevance');

      if (cfg?.supported_formats) {
        if (mode === 'initial') {
          setAdvancedFilters(prev => ({
            ...prev,
            formats: cfg.supported_formats,
            sort: defaultSort,
          }));
        } else if (mode === 'settings-saved') {
          // On settings save, update formats and reset sort to new default
          setAdvancedFilters(prev => ({
            ...prev,
            formats: prev.formats.filter(f => cfg.supported_formats.includes(f)),
            sort: defaultSort,
          }));
        }
      }
    } catch (error) {
      console.error('Failed to load config:', error);
    }
  }, [clearTracking, contentType, setAdvancedFilters, setBooks]);

  // Fetch config when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      loadConfig('initial');
    }
  }, [isAuthenticated, loadConfig]);

  const effectiveSearchMode: SearchMode = config?.search_mode ?? 'direct';
  const defaultMetadataProviderForContentType = getConfiguredMetadataProviderForContentType({
    contentType,
    configuredMetadataProvider,
    configuredAudiobookMetadataProvider,
  });
  const effectiveMetadataProvider = effectiveSearchMode === 'universal'
    ? (defaultMetadataProviderForContentType || null)
    : null;
  const resolvedMetadataSortOptions = useMemo(
    () => activeMetadataConfig?.sort_options ?? config?.metadata_sort_options ?? [],
    [activeMetadataConfig?.sort_options, config?.metadata_sort_options],
  );
  const resolvedMetadataDefaultSort = useMemo(() => getEffectiveMetadataSort({
    currentSort: '',
    defaultSort: activeMetadataConfig?.default_sort || config?.metadata_default_sort || 'relevance',
    sortOptions: resolvedMetadataSortOptions,
  }), [activeMetadataConfig?.default_sort, config?.metadata_default_sort, resolvedMetadataSortOptions]);
  const prevMetadataSortContextRef = useRef<string>('');

  // Non-admins in universal mode have nothing in the advanced panel
  const hasAdvancedContent = requestRoleIsAdmin || effectiveSearchMode === 'direct';

  useEffect(() => {
    if (!hasAdvancedContent && showAdvanced) {
      setShowAdvanced(false);
    }
  }, [hasAdvancedContent, showAdvanced, setShowAdvanced]);

  useEffect(() => {
    let isMounted = true;

    if (!isAuthenticated || effectiveSearchMode !== 'universal') {
      setActiveMetadataConfig(null);
      return () => {
        isMounted = false;
      };
    }

    const loadMetadataConfig = async () => {
      try {
        const nextConfig = await getMetadataSearchConfig(
          contentType,
          effectiveMetadataProvider ?? undefined,
        );
        if (isMounted) {
          setActiveMetadataConfig(nextConfig);
        }
      } catch (error) {
        console.error('Failed to load metadata search config:', error);
        if (isMounted) {
          setActiveMetadataConfig(null);
        }
      }
    };

    void loadMetadataConfig();

    return () => {
      isMounted = false;
    };
  }, [isAuthenticated, effectiveSearchMode, contentType, effectiveMetadataProvider]);

  useEffect(() => {
    if (effectiveSearchMode !== 'universal') {
      prevMetadataSortContextRef.current = '';
      return;
    }

    const metadataSortContext = [
      contentType,
      effectiveMetadataProvider ?? '',
      resolvedMetadataDefaultSort,
      resolvedMetadataSortOptions.map((option) => option.value).join(','),
    ].join('::');
    const contextChanged = prevMetadataSortContextRef.current !== ''
      && prevMetadataSortContextRef.current !== metadataSortContext;
    const nextSort = contextChanged
      ? resolvedMetadataDefaultSort
      : getEffectiveMetadataSort({
          currentSort: advancedFilters.sort,
          defaultSort: resolvedMetadataDefaultSort,
          sortOptions: resolvedMetadataSortOptions,
        });

    prevMetadataSortContextRef.current = metadataSortContext;

    if (nextSort !== advancedFilters.sort) {
      setAdvancedFilters((prev) => ({ ...prev, sort: nextSort }));
    }
  }, [
    advancedFilters.sort,
    contentType,
    effectiveMetadataProvider,
    effectiveSearchMode,
    resolvedMetadataDefaultSort,
    resolvedMetadataSortOptions,
    setAdvancedFilters,
  ]);

  const prevEffectiveSearchModeRef = useRef<SearchMode>(effectiveSearchMode);
  useEffect(() => {
    if (prevEffectiveSearchModeRef.current !== effectiveSearchMode) {
      setBooks([]);
      setSelectedBook(null);
      setReleaseBook(null);
      setActiveResultsSort('');
      clearTracking();
      prevEffectiveSearchModeRef.current = effectiveSearchMode;
    }
  }, [effectiveSearchMode, setBooks, clearTracking]);

  const runSearchWithPolicyRefresh = useCallback(
    (opts: {
      query: string;
      fieldValues?: Record<string, string | number | boolean>;
      contentTypeOverride?: ContentType;
      searchModeOverride?: SearchMode;
      providerOverride?: string;
    }) => {
      void refreshRequestPolicy();
      void handleSearch({
        query: opts.query,
        config,
        fieldValues: opts.fieldValues,
        contentTypeOverride: opts.contentTypeOverride,
        searchMode: opts.searchModeOverride,
        providerOverride: opts.providerOverride,
      });
    },
    [refreshRequestPolicy, handleSearch, config]
  );

  // Execute URL-based search when params are present
  useEffect(() => {
    if (
      wasProcessed &&
      parsedParams &&
      !urlSearchExecutedRef.current &&
      config
    ) {
      urlSearchExecutedRef.current = true;

      const parsedSearchMode = config.search_mode || 'direct';
      const urlContentTypeOverride =
        parsedSearchMode === 'universal' ? parsedParams.contentType : undefined;

      if (urlContentTypeOverride && urlContentTypeOverride !== contentType) {
        setContentType(urlContentTypeOverride);
      }

      if (!parsedParams.hasSearchParams) {
        return;
      }
      const bookLanguages = config.book_languages || [];
      const defaultLanguageCodes =
        config.default_language && config.default_language.length > 0
          ? config.default_language
          : [bookLanguages[0]?.code || 'en'];

      // Populate search input from URL
      if (parsedParams.searchInput) {
        setSearchInput(parsedParams.searchInput);
      }

      let nextQueryTarget = 'general';
      if (parsedSearchMode === 'direct') {
        if (parsedParams.advancedFilters.isbn) {
          nextQueryTarget = 'isbn';
        } else if (parsedParams.advancedFilters.author) {
          nextQueryTarget = 'author';
        } else if (parsedParams.advancedFilters.title) {
          nextQueryTarget = 'title';
        }
      }
      setActiveQueryTarget(nextQueryTarget);

      const resolvedUrlMetadataSort = parsedSearchMode === 'universal'
        ? getEffectiveMetadataSort({
            currentSort: typeof parsedParams.advancedFilters.sort === 'string'
              ? parsedParams.advancedFilters.sort
              : '',
            defaultSort: resolvedMetadataDefaultSort,
            sortOptions: resolvedMetadataSortOptions,
          })
        : parsedParams.advancedFilters.sort;

      // Apply advanced filters from URL
      if (Object.keys(parsedParams.advancedFilters).length > 0) {
        setAdvancedFilters(prev => ({
          ...prev,
          ...parsedParams.advancedFilters,
          ...(parsedSearchMode === 'universal' && resolvedUrlMetadataSort
            ? { sort: resolvedUrlMetadataSort }
            : {}),
        }));

        const hasAdvancedValues = ['content', 'lang', 'formats'].some(
          key => parsedParams.advancedFilters[key as keyof typeof parsedParams.advancedFilters]
        );
        if (hasAdvancedValues) {
          setShowAdvanced(true);
        }
      }

      // Build query and trigger search
      const mergedFilters = {
        ...advancedFilters,
        ...parsedParams.advancedFilters,
        ...(parsedSearchMode === 'universal' && resolvedUrlMetadataSort
          ? { sort: resolvedUrlMetadataSort }
          : {}),
      };

      const query = buildSearchQuery({
        searchInput:
          parsedSearchMode === 'direct' && nextQueryTarget !== 'general'
            ? ''
            : parsedParams.searchInput,
        showAdvanced: true,
        advancedFilters: {
          ...(mergedFilters as typeof advancedFilters),
          isbn: nextQueryTarget === 'isbn' ? String(parsedParams.advancedFilters.isbn || '') : '',
          author: nextQueryTarget === 'author' ? String(parsedParams.advancedFilters.author || '') : '',
          title: nextQueryTarget === 'title' ? String(parsedParams.advancedFilters.title || '') : '',
        },
        bookLanguages,
        defaultLanguage: defaultLanguageCodes,
        searchMode: parsedSearchMode,
      });

      runSearchWithPolicyRefresh({
        query,
        contentTypeOverride: urlContentTypeOverride,
        searchModeOverride: parsedSearchMode,
      });
    }
  }, [
    wasProcessed,
    parsedParams,
    contentType,
    config,
    advancedFilters,
    resolvedMetadataDefaultSort,
    resolvedMetadataSortOptions,
    runSearchWithPolicyRefresh,
    setSearchInput,
    setAdvancedFilters,
    setShowAdvanced,
    setActiveQueryTarget,
  ]);

  const handleSettingsSaved = useCallback(() => {
    loadConfig('settings-saved');
  }, [loadConfig]);

  // Log WebSocket connection status
  useEffect(() => {
    if (isUsingWebSocket) {
      console.log('✅ Using WebSocket for real-time updates');
    } else {
      console.log('⏳ Using polling fallback (5s interval)');
    }
  }, [isUsingWebSocket]);

  // Fetch status on startup
  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  // Show book details
  const handleShowDetails = async (id: string): Promise<void> => {
    const book = books.find((entry) => entry.id === id);
    const metadataBook = book && isMetadataBook(book) ? book : null;

    if (metadataBook) {
      try {
        const fullBook = await getMetadataBookInfo(metadataBook.provider!, metadataBook.provider_id!);
        setSelectedBook({
          ...metadataBook,
          description: fullBook.description || metadataBook.description,
          series_id: fullBook.series_id || metadataBook.series_id,
          series_name: fullBook.series_name,
          series_position: fullBook.series_position,
          series_count: fullBook.series_count,
        });
      } catch (error) {
        console.error('Failed to load book description, using search data:', error);
        setSelectedBook(metadataBook);
      }
    } else {
      try {
        if (!book?.source) {
          throw new Error('Book is missing source context');
        }
        const fullBook = await getSourceRecordInfo(book.source, id);
        setSelectedBook(fullBook);
      } catch (error) {
        console.error('Failed to load book details, using search data:', error);
        if (book) {
          setSelectedBook(book);
        } else {
          showToast('Failed to load book details', 'error');
        }
      }
    }
  };

  const submitRequest = useCallback(
    async (payload: CreateRequestPayload, successMessage: string): Promise<boolean> => {
      try {
        await createRequest(payload);
        await refreshActivitySnapshot();
        showToast(successMessage, 'success');
        await refreshRequestPolicy({ force: true });
        return true;
      } catch (error) {
        console.error('Request creation failed:', error);
        showToast(getErrorMessage(error, 'Failed to create request'), 'error');
        if (isPolicyGuardError(error)) {
          await refreshRequestPolicy({ force: true });
        }
        return false;
      }
    },
    [showToast, refreshRequestPolicy, refreshActivitySnapshot]
  );

  const openRequestConfirmation = useCallback((payload: CreateRequestPayload) => {
    setPendingRequestPayload(payload);
  }, []);

  const handleConfirmRequest = useCallback(
    async (payload: CreateRequestPayload): Promise<boolean> => {
      const success = await submitRequest(payload, getRequestSuccessMessage(payload));
      if (success) {
        setPendingRequestPayload(null);
      }
      return success;
    },
    [submitRequest]
  );

  const getDirectPolicyMode = useCallback((book: Book): RequestPolicyMode => {
    return getSourceMode(getBrowseSource(book), 'ebook');
  }, [getSourceMode]);

  const getUniversalDefaultPolicyMode = useCallback((): RequestPolicyMode => {
    return getDefaultMode(contentType);
  }, [getDefaultMode, contentType]);

  const buildReleaseDownloadPayload = useCallback(
    (book: Book, release: Release, releaseContentType: ContentType): DownloadReleasePayload => {
      const isManual = book.provider === 'manual';
      const releasePreview = typeof release.extra?.preview === 'string' ? release.extra.preview : undefined;
      const releaseAuthor = typeof release.extra?.author === 'string' ? release.extra.author : undefined;

      return {
        source: release.source,
        source_id: release.source_id,
        title: isManual ? release.title : book.title,
        author: isManual ? (releaseAuthor || '') : book.author,
        year: book.year,
        format: release.format,
        size: release.size,
        size_bytes: release.size_bytes,
        download_url: release.download_url,
        protocol: release.protocol,
        indexer: release.indexer,
        seeders: release.seeders,
        extra: release.extra,
        preview: isManual ? (releasePreview || undefined) : book.preview,
        content_type: releaseContentType,
        series_name: book.series_name,
        series_position: book.series_position,
        subtitle: book.subtitle,
      };
    },
    []
  );

  // When downloading a book while browsing a Hardcover list, automatically
  // remove it from that list (fire-and-forget).
  const searchFieldLabelsRef = useRef(searchFieldLabels);
  searchFieldLabelsRef.current = searchFieldLabels;

  const removeBookFromActiveList = useCallback((book: Book) => {
    if (!bookSupportsTargets(book)) return;
    const activeList = searchFieldValuesRef.current.hardcover_list;
    if (!activeList) return;
    const target = String(activeList);
    void setBookTargetState(book.provider!, book.provider_id!, target, false).then((result) => {
      if (result.changed) {
        emitBookTargetChange({
          provider: book.provider!,
          bookId: book.provider_id!,
          target,
          selected: false,
        });
        const listName = searchFieldLabelsRef.current['hardcover_list'];
        showToast(`Removed from ${listName || 'list'}`, 'info');
      }
    }).catch(() => {});
  }, [showToast]);

  const executeBookDownload = useCallback(
    async (book: Book, onBehalfOfUserId?: number): Promise<void> => {
      const source = getBrowseSource(book);
      const directContentType: ContentType = 'ebook';
      try {
        await downloadRelease(buildReleaseDataFromDirectBook(book), onBehalfOfUserId);
        await fetchStatus();
        removeBookFromActiveList(book);
      } catch (error) {
        console.error('Download failed:', error);
        if (isPolicyGuardError(error)) {
          const requiredMode = getPolicyGuardRequiredMode(error);
          policyTrace('direct.action:policy_guard', {
            bookId: book.id,
            source,
            contentType: directContentType,
            requiredMode,
            code: isApiResponseError(error) ? error.code : null,
          });
          if (requiredMode === 'request_release') {
            openRequestConfirmation(buildDirectRequestPayload(book));
            await refreshRequestPolicy({ force: true });
            return;
          }
          showToast('Download blocked by policy', 'error');
          await refreshRequestPolicy({ force: true });
          return;
        }
        showToast(getErrorMessage(error, 'Failed to queue download'), 'error');
        throw error;
      }
    },
    [fetchStatus, openRequestConfirmation, refreshRequestPolicy, removeBookFromActiveList, showToast]
  );

  const executeReleaseDownload = useCallback(
    async (
      book: Book,
      release: Release,
      releaseContentType: ContentType,
      onBehalfOfUserId?: number
    ): Promise<void> => {
      try {
        trackRelease(book.id, release.source_id);
        await downloadRelease(
          buildReleaseDownloadPayload(book, release, releaseContentType),
          onBehalfOfUserId
        );
        await fetchStatus();
        removeBookFromActiveList(book);
      } catch (error) {
        console.error('Release download failed:', error);
        if (isPolicyGuardError(error)) {
          const requiredMode = getPolicyGuardRequiredMode(error);
          const normalizedContentType = toContentType(releaseContentType);
          policyTrace('release.action:policy_guard', {
            bookId: book.id,
            releaseId: release.source_id,
            source: release.source,
            requiredMode,
            code: isApiResponseError(error) ? error.code : null,
            contentType: normalizedContentType,
          });
          if (requiredMode === 'request_release') {
            openRequestConfirmation({
              book_data: buildMetadataBookRequestData(book, normalizedContentType),
              release_data: buildReleaseDataFromMetadataRelease(book, release, normalizedContentType),
              context: {
                source: release.source,
                content_type: normalizedContentType,
                request_level: 'release',
              },
            });
            await refreshRequestPolicy({ force: true });
            return;
          }
          if (requiredMode === 'request_book') {
            setReleaseBook(null);
            openRequestConfirmation({
              book_data: buildMetadataBookRequestData(book, normalizedContentType),
              release_data: null,
              context: {
                source: release.source,
                content_type: normalizedContentType,
                request_level: 'book',
              },
            });
            await refreshRequestPolicy({ force: true });
            return;
          }
          showToast('Download blocked by policy', 'error');
          await refreshRequestPolicy({ force: true });
          return;
        }
        showToast(getErrorMessage(error, 'Failed to queue download'), 'error');
        throw error;
      }
    },
    [buildReleaseDownloadPayload, fetchStatus, openRequestConfirmation, refreshRequestPolicy, removeBookFromActiveList, showToast, trackRelease]
  );

  const handleConfirmOnBehalfDownload = useCallback(async (): Promise<boolean> => {
    if (!pendingOnBehalfDownload) {
      return true;
    }

    const onBehalfOfUserId = pendingOnBehalfDownload.actingAsUser.id;
    try {
      if (pendingOnBehalfDownload.type === 'book') {
        await executeBookDownload(pendingOnBehalfDownload.book, onBehalfOfUserId);
      } else {
        await executeReleaseDownload(
          pendingOnBehalfDownload.book,
          pendingOnBehalfDownload.release,
          pendingOnBehalfDownload.releaseContentType,
          onBehalfOfUserId
        );
      }
      setPendingOnBehalfDownload(null);
      return true;
    } catch {
      return false;
    }
  }, [executeBookDownload, executeReleaseDownload, pendingOnBehalfDownload]);

  // Direct-mode action (download or release-level request based on policy).
  const handleDownload = async (book: Book): Promise<void> => {
    const source = getBrowseSource(book);
    const directContentType: ContentType = 'ebook';
    let mode = getDirectPolicyMode(book);
    policyTrace('direct.action:start', {
      bookId: book.id,
      source,
      contentType: directContentType,
      cachedMode: mode,
      isAdmin: requestRoleIsAdmin,
    });
    try {
      const latestPolicy = await refreshRequestPolicy({ force: true });
      const effectiveIsAdmin = latestPolicy ? Boolean(latestPolicy.is_admin) : requestRoleIsAdmin;
      mode = resolveSourceModeFromPolicy(latestPolicy, effectiveIsAdmin, source, directContentType);
      policyTrace('direct.action:resolved', {
        bookId: book.id,
        source,
        contentType: directContentType,
        resolvedMode: mode,
        effectiveIsAdmin,
        defaults: latestPolicy?.defaults ?? null,
        requestsEnabled: latestPolicy?.requests_enabled ?? null,
      });
    } catch (error) {
      console.warn('Failed to refresh request policy before direct action:', error);
      policyTrace('direct.action:refresh_failed', {
        bookId: book.id,
        source,
        contentType: directContentType,
        mode,
        message: error instanceof Error ? error.message : String(error),
      });
    }

    if (mode === 'blocked') {
      policyTrace('direct.action:block', { bookId: book.id, mode });
      showToast('Download blocked by policy', 'error');
      await refreshRequestPolicy({ force: true });
      return;
    }

    if (mode === 'request_release') {
      policyTrace('direct.action:request_modal', { bookId: book.id, mode });
      openRequestConfirmation(buildDirectRequestPayload(book));
      return;
    }

    if (actingAsUser) {
      setPendingOnBehalfDownload({
        type: 'book',
        book,
        actingAsUser,
      });
      return;
    }

    await executeBookDownload(book);
  };

  // Cancel download
  const handleCancel = async (id: string) => {
    try {
      await cancelDownload(id);
      await fetchStatus();
    } catch (error) {
      console.error('Cancel failed:', error);
      showToast('Failed to cancel/clear download', 'error');
    }
  };

  const handleRetry = async (id: string) => {
    try {
      await retryDownload(id);
      await fetchStatus();
    } catch (error) {
      console.error('Retry failed:', error);
      showToast('Failed to retry download', 'error');
    }
  };

  // Universal-mode "Get" action (open releases, request-book, or block by policy).
  const handleGetReleases = async (book: Book) => {
    let mode = getUniversalDefaultPolicyMode();
    const normalizedContentType = toContentType(contentType);
    policyTrace('universal.get:start', {
      bookId: book.id,
      contentType: normalizedContentType,
      cachedMode: mode,
      isAdmin: requestRoleIsAdmin,
    });
    try {
      const latestPolicy = await refreshRequestPolicy({ force: true });
      const effectiveIsAdmin = latestPolicy ? Boolean(latestPolicy.is_admin) : requestRoleIsAdmin;
      mode = resolveDefaultModeFromPolicy(latestPolicy, effectiveIsAdmin, contentType);
      policyTrace('universal.get:resolved', {
        bookId: book.id,
        contentType: normalizedContentType,
        resolvedMode: mode,
        effectiveIsAdmin,
        defaults: latestPolicy?.defaults ?? null,
        requestsEnabled: latestPolicy?.requests_enabled ?? null,
      });
    } catch (error) {
      console.warn('Failed to refresh request policy before universal action:', error);
      policyTrace('universal.get:refresh_failed', {
        bookId: book.id,
        contentType: normalizedContentType,
        mode,
        message: error instanceof Error ? error.message : String(error),
      });
    }

    if (mode === 'blocked') {
      policyTrace('universal.get:block', { bookId: book.id, contentType: normalizedContentType });
      showToast('This title is unavailable by policy', 'error');
      return;
    }

    if (mode === 'request_book') {
      policyTrace('universal.get:request_modal', {
        bookId: book.id,
        requestLevel: 'book',
        contentType: normalizedContentType,
      });
      openRequestConfirmation({
        book_data: buildMetadataBookRequestData(book, normalizedContentType),
        release_data: null,
        context: {
          source: '*',
          content_type: normalizedContentType,
          request_level: 'book',
        },
      });
      return;
    }

    if (book.provider && book.provider_id) {
      try {
        policyTrace('universal.get:open_release_modal', {
          bookId: book.id,
          contentType: normalizedContentType,
        });
        const fullBook = await getMetadataBookInfo(book.provider, book.provider_id);
        setReleaseBook({
          ...book,
          description: fullBook.description || book.description,
          series_id: fullBook.series_id || book.series_id,
          series_name: fullBook.series_name,
          series_position: fullBook.series_position,
          series_count: fullBook.series_count,
        });
      } catch (error) {
        console.error('Failed to load book description, using search data:', error);
        policyTrace('universal.get:open_release_modal_fallback', {
          bookId: book.id,
          contentType: normalizedContentType,
          message: error instanceof Error ? error.message : String(error),
        });
        setReleaseBook(book);
      }
    } else {
      policyTrace('universal.get:open_release_modal_no_provider', {
        bookId: book.id,
        contentType: normalizedContentType,
      });
      setReleaseBook(book);
    }
  };

  // Handle download from ReleaseModal (universal mode release rows).
  const handleReleaseDownload = async (book: Book, release: Release, releaseContentType: ContentType) => {
    policyTrace('release.action:start', {
      bookId: book.id,
      releaseId: release.source_id,
      source: release.source,
      contentType: toContentType(releaseContentType),
    });

    if (actingAsUser) {
      setPendingOnBehalfDownload({
        type: 'release',
        book,
        release,
        releaseContentType,
        actingAsUser,
      });
      return;
    }

    await executeReleaseDownload(book, release, releaseContentType);
  };

  const handleReleaseRequest = useCallback(
    async (book: Book, release: Release, releaseContentType: ContentType): Promise<void> => {
      void refreshRequestPolicy();
      const normalizedContentType = toContentType(releaseContentType);
      openRequestConfirmation({
        book_data: buildMetadataBookRequestData(book, normalizedContentType),
        release_data: buildReleaseDataFromMetadataRelease(book, release, normalizedContentType),
        context: {
          source: release.source,
          content_type: normalizedContentType,
          request_level: 'release',
        },
      });
    },
    [openRequestConfirmation, refreshRequestPolicy]
  );

  const handleReleaseBookRequest = useCallback(
    async (book: Book, modalContentType: ContentType): Promise<void> => {
      void refreshRequestPolicy();
      const normalizedContentType = toContentType(modalContentType);
      openRequestConfirmation({
        book_data: buildMetadataBookRequestData(book, normalizedContentType),
        release_data: null,
        context: {
          source: '*',
          content_type: normalizedContentType,
          request_level: 'book',
        },
      });
    },
    [openRequestConfirmation, refreshRequestPolicy]
  );

  const handleReleaseModalPolicyRefresh = useCallback(() => {
    return refreshRequestPolicy({ force: true });
  }, [refreshRequestPolicy]);

  const handleRequestCancel = useCallback(
    async (requestId: number) => {
      try {
        await cancelUserRequest(requestId);
        await refreshActivitySnapshot();
        showToast('Request cancelled', 'success');
      } catch (error) {
        showToast(getErrorMessage(error, 'Failed to cancel request'), 'error');
      }
    },
    [cancelUserRequest, refreshActivitySnapshot, showToast]
  );

  const handleRequestReject = useCallback(
    async (requestId: number, adminNote?: string) => {
      if (!requestRoleIsAdmin) {
        return;
      }

      try {
        await rejectSidebarRequest(requestId, adminNote);
        await refreshActivitySnapshot();
        showToast('Request rejected', 'success');
      } catch (error) {
        showToast(getErrorMessage(error, 'Failed to reject request'), 'error');
      }
    },
    [refreshActivitySnapshot, requestRoleIsAdmin, rejectSidebarRequest, showToast]
  );

  const handleRequestApprove = useCallback(
    async (
      requestId: number,
      record: RequestRecord,
      options?: {
        browseOnly?: boolean;
        manualApproval?: boolean;
      }
    ) => {
      if (!requestRoleIsAdmin) {
        return;
      }

      if (options?.manualApproval) {
        try {
          await fulfilSidebarRequest(requestId, undefined, undefined, true);
          await refreshActivitySnapshot();
          showToast('Request approved', 'success');
          await fetchStatus();
        } catch (error) {
          showToast(getErrorMessage(error, 'Failed to approve request'), 'error');
        }
        return;
      }

      const shouldBrowse = Boolean(options?.browseOnly) || record.request_level === 'book';

      if (!shouldBrowse && record.request_level === 'release') {
        try {
          await fulfilSidebarRequest(requestId, record.release_data || undefined);
          await refreshActivitySnapshot();
          showToast('Request approved', 'success');
          await fetchStatus();
        } catch (error) {
          showToast(getErrorMessage(error, 'Failed to approve request'), 'error');
        }
        return;
      }

      setReleaseBook(null);
      setFulfillingRequest({
        requestId,
        book: bookFromRequestData(record.book_data),
        contentType: record.content_type,
      });
    },
    [requestRoleIsAdmin, fulfilSidebarRequest, showToast, fetchStatus, refreshActivitySnapshot]
  );

  const handleBrowseFulfilDownload = useCallback(
    async (book: Book, release: Release, releaseContentType: ContentType) => {
      if (!fulfillingRequest) {
        return;
      }

      try {
        await fulfilSidebarRequest(
          fulfillingRequest.requestId,
          buildReleaseDataFromMetadataRelease(book, release, toContentType(releaseContentType))
        );
        await refreshActivitySnapshot();
        showToast(`Request approved: ${book.title || 'Untitled'}`, 'success');
        setFulfillingRequest(null);
        await fetchStatus();
      } catch (error) {
        console.error('Browse fulfil failed:', error);
        showToast(getErrorMessage(error, 'Failed to fulfil request'), 'error');
        throw error;
      }
    },
    [fulfillingRequest, fulfilSidebarRequest, showToast, fetchStatus, refreshActivitySnapshot]
  );

  const getDirectActionButtonState = useCallback(
    (bookId: string): ButtonStateInfo => {
      const baseState = getButtonState(bookId);
      const book = books.find((entry) => entry.id === bookId);
      if (!book) {
        return baseState;
      }
      if (baseState.state === 'complete' && isDownloadTaskDismissed(bookId)) {
        return applyDirectPolicyModeToButtonState(
          { text: 'Download', state: 'download' },
          getDirectPolicyMode(book)
        );
      }
      const mode = getDirectPolicyMode(book);
      return applyDirectPolicyModeToButtonState(baseState, mode);
    },
    [books, getButtonState, getDirectPolicyMode, isDownloadTaskDismissed]
  );

  const getUniversalActionButtonState = useCallback(
    (bookId: string): ButtonStateInfo => {
      const baseState = getUniversalButtonState(bookId);
      const trackedReleaseIds = bookToReleaseMap[bookId] || [];
      const allTrackedReleasesDismissed = trackedReleaseIds.length > 0 &&
        trackedReleaseIds.every((releaseId) => isDownloadTaskDismissed(releaseId));

      if (
        baseState.state === 'complete' &&
        (isDownloadTaskDismissed(bookId) || allTrackedReleasesDismissed)
      ) {
        return applyUniversalPolicyModeToButtonState(
          { text: 'Get', state: 'download' },
          getUniversalDefaultPolicyMode()
        );
      }
      const mode = getUniversalDefaultPolicyMode();
      return applyUniversalPolicyModeToButtonState(baseState, mode);
    },
    [bookToReleaseMap, getUniversalButtonState, getUniversalDefaultPolicyMode, isDownloadTaskDismissed]
  );

  const bookLanguages = config?.book_languages || DEFAULT_LANGUAGES;
  const supportedFormats = config?.supported_formats || DEFAULT_SUPPORTED_FORMATS;
  const defaultLanguageCodes =
    config?.default_language && config.default_language.length > 0
      ? config.default_language
      : [bookLanguages[0]?.code || 'en'];

  const logoUrl = withBasePath('/logo.png');

  // Manual search is only allowed when the default policy permits browsing releases
  const universalDefaultMode = getUniversalDefaultPolicyMode();
  const manualSearchAllowed = effectiveSearchMode === 'universal'
    && (universalDefaultMode === 'download' || universalDefaultMode === 'request_release');

  const queryTargets = useMemo<QueryTargetOption[]>(
    () => buildQueryTargets({
      searchMode: effectiveSearchMode,
      metadataSearchFields: activeMetadataConfig?.search_fields ?? [],
      manualSearchAllowed,
    }),
    [effectiveSearchMode, activeMetadataConfig?.search_fields, manualSearchAllowed],
  );

  useEffect(() => {
    setActiveQueryTarget((prev) => {
      if (queryTargets.some((target) => target.key === prev)) return prev;
      return getDefaultQueryTargetKey(queryTargets);
    });
  }, [queryTargets]);

  const activeQueryOption = useMemo(
    () => queryTargets.find((target) => target.key === activeQueryTarget) ?? queryTargets[0],
    [queryTargets, activeQueryTarget],
  );

  const activeQueryField = activeQueryOption?.field ?? null;
  const seriesBrowseCapability = useMemo(
    () => activeMetadataConfig?.capabilities.find((capability) =>
      capability.key === 'view_series'
      && capability.field_key
    ) ?? null,
    [activeMetadataConfig?.capabilities],
  );
  const seriesBrowseTarget = useMemo(
    () => seriesBrowseCapability?.field_key
      ? queryTargets.find((target) => target.field?.key === seriesBrowseCapability.field_key) ?? null
      : null,
    [queryTargets, seriesBrowseCapability?.field_key],
  );

  const activeQueryValue = useMemo(() => {
    if (!activeQueryOption || activeQueryOption.source === 'general' || activeQueryOption.source === 'manual') {
      return searchInput;
    }

    if (activeQueryOption.source === 'direct-field') {
      if (activeQueryOption.key === 'isbn') return advancedFilters.isbn;
      if (activeQueryOption.key === 'author') return advancedFilters.author;
      if (activeQueryOption.key === 'title') return advancedFilters.title;
      return '';
    }

    if (!activeQueryOption.field) {
      return '';
    }

    if (activeQueryOption.field.type === 'CheckboxSearchField') {
      return searchFieldValues[activeQueryOption.field.key] ?? activeQueryOption.field.default ?? false;
    }

    return searchFieldValues[activeQueryOption.field.key] ?? '';
  }, [activeQueryOption, searchInput, advancedFilters, searchFieldValues]);

  const activeQueryValueLabel = useMemo(() => {
    if (!activeQueryOption?.field) {
      return undefined;
    }
    return searchFieldLabels[activeQueryOption.field.key];
  }, [activeQueryOption, searchFieldLabels]);
  const activeQueryUsesSeriesBrowse = Boolean(
    seriesBrowseCapability?.field_key
    && activeQueryOption?.source === 'provider-field'
    && activeQueryOption.field?.key === seriesBrowseCapability.field_key
    && activeQueryValue !== ''
    && activeQueryValue !== false,
  );
  const activeQueryUsesListBrowse = Boolean(
    activeQueryOption?.source === 'provider-field'
    && activeQueryOption.field?.type === 'DynamicSelectSearchField'
    && activeQueryValue !== ''
    && activeQueryValue !== false,
  );
  const effectiveMetadataSort = getEffectiveMetadataSort({
    currentSort: advancedFilters.sort,
    defaultSort: resolvedMetadataDefaultSort,
    sortOptions: resolvedMetadataSortOptions,
  });
  const visibleResultsSort = activeResultsSort || (
    effectiveSearchMode === 'universal' ? effectiveMetadataSort : advancedFilters.sort
  );

  const getAppliedUniversalSort = useCallback((sortOverride?: string) => {
    const requestedSort = sortOverride ?? effectiveMetadataSort;
    const seriesBrowseSort = seriesBrowseCapability?.sort ?? '';

    if (activeQueryUsesSeriesBrowse && seriesBrowseSort) {
      return seriesBrowseSort;
    }

    return requestedSort;
  }, [activeQueryUsesSeriesBrowse, effectiveMetadataSort, seriesBrowseCapability?.sort]);

  const handleActiveQueryValueChange = useCallback((value: string | number | boolean, label?: string) => {
    if (!activeQueryOption || activeQueryOption.source === 'general' || activeQueryOption.source === 'manual') {
      setSearchInput(typeof value === 'string' ? value : String(value ?? ''));
      return;
    }

    if (activeQueryOption.source === 'direct-field') {
      const nextValue = typeof value === 'string' ? value : String(value ?? '');
      if (activeQueryOption.key === 'isbn') {
        updateAdvancedFilters({ isbn: nextValue });
      } else if (activeQueryOption.key === 'author') {
        updateAdvancedFilters({ author: nextValue });
      } else if (activeQueryOption.key === 'title') {
        updateAdvancedFilters({ title: nextValue });
      }
      return;
    }

    if (activeQueryOption.field) {
      updateSearchFieldValue(activeQueryOption.field.key, value, label);
    }
  }, [activeQueryOption, setSearchInput, updateAdvancedFilters, updateSearchFieldValue]);

  const handleSearchModeChange = useCallback((nextMode: SearchMode) => {
    setConfig((prev) => prev ? { ...prev, search_mode: nextMode } : prev);
    updateSelfUser({ settings: { SEARCH_MODE: nextMode } })
      .then(() => loadConfig('settings-saved'))
      .catch((err) => console.error('Failed to save search mode:', err));
  }, [loadConfig]);

  const handleMetadataProviderChange = useCallback((provider: string) => {
    if (contentType === 'audiobook') {
      setConfiguredAudiobookMetadataProvider(provider);
    } else {
      setConfiguredMetadataProvider(provider);
    }
    const key = contentType === 'audiobook' ? 'METADATA_PROVIDER_AUDIOBOOK' : 'METADATA_PROVIDER';
    updateSelfUser({ settings: { [key]: provider } })
      .then(() => loadConfig('settings-saved'))
      .catch((err) => console.error('Failed to save metadata provider:', err));
  }, [contentType, loadConfig]);

  const buildCurrentSearchRequest = useCallback((sortOverride?: string) => {
    const appliedSort = effectiveSearchMode === 'universal'
      ? getAppliedUniversalSort(sortOverride)
      : (sortOverride ?? advancedFilters.sort);
    const nextFilters = appliedSort === advancedFilters.sort && sortOverride === undefined
      ? advancedFilters
      : { ...advancedFilters, sort: appliedSort };

    if (effectiveSearchMode === 'direct') {
      const directFilters = {
        ...nextFilters,
        isbn: '',
        author: '',
        title: '',
      };

      if (activeQueryOption?.source === 'direct-field') {
        const nextValue = typeof activeQueryValue === 'string' ? activeQueryValue : String(activeQueryValue ?? '');
        if (activeQueryOption.key === 'isbn') {
          directFilters.isbn = nextValue;
        } else if (activeQueryOption.key === 'author') {
          directFilters.author = nextValue;
        } else if (activeQueryOption.key === 'title') {
          directFilters.title = nextValue;
        }
      }

      const query = buildSearchQuery({
        searchInput: activeQueryOption?.source === 'general' ? searchInput : '',
        showAdvanced: true,
        advancedFilters: directFilters,
        bookLanguages,
        defaultLanguage: defaultLanguageCodes,
        searchMode: effectiveSearchMode,
      });

      return {
        query,
        fieldValues: {},
        providerOverride: undefined,
        appliedSort,
      };
    }

    const fieldValues =
      activeQueryOption?.source === 'provider-field'
      && activeQueryOption.field
      && activeQueryValue !== ''
      && activeQueryValue !== false
        ? { [activeQueryOption.field.key]: activeQueryValue }
        : {};

    const query = buildSearchQuery({
      searchInput:
        activeQueryOption?.source === 'general' || activeQueryOption?.source === 'manual'
          ? searchInput
          : '',
      showAdvanced: true,
      advancedFilters: nextFilters,
      bookLanguages,
      defaultLanguage: defaultLanguageCodes,
      searchMode: effectiveSearchMode,
    });

    return {
      query,
      fieldValues,
      providerOverride: effectiveMetadataProvider ?? undefined,
      appliedSort,
    };
  }, [
    activeQueryOption,
    activeQueryValue,
    advancedFilters,
    bookLanguages,
    defaultLanguageCodes,
    effectiveMetadataProvider,
    effectiveSearchMode,
    getAppliedUniversalSort,
    searchInput,
  ]);

  // Handle "View Series" - trigger search with series field and series order sort
  const handleSearchSeries = useCallback((seriesName: string, seriesId?: string) => {
    const seriesTarget = seriesBrowseTarget;
    const seriesFieldKey = seriesTarget?.field?.key;
    const seriesSort = seriesBrowseCapability?.sort;
    if (!seriesTarget || !seriesFieldKey || !seriesSort) {
      return;
    }

    // Clear UI state
    setSearchInput('');
    setSelectedBook(null);
    setReleaseBook(null);
    clearTracking();

    const seriesFilters = { ...advancedFilters, sort: seriesSort };
    setActiveResultsSort(seriesSort);

    setActiveQueryTarget(seriesTarget.key);
    updateSearchFieldValue(
      seriesFieldKey,
      seriesId ? `id:${seriesId}` : seriesName,
      seriesName,
    );

    const query = buildSearchQuery({
      searchInput: '',
      showAdvanced: true,
      advancedFilters: seriesFilters,
      bookLanguages,
      defaultLanguage: defaultLanguageCodes,
      searchMode: effectiveSearchMode,
    });

    runSearchWithPolicyRefresh({
      query,
      fieldValues: { [seriesFieldKey]: seriesId ? `id:${seriesId}` : seriesName },
      searchModeOverride: effectiveSearchMode,
      providerOverride: effectiveMetadataProvider ?? undefined,
    });
  }, [
    advancedFilters,
    bookLanguages,
    clearTracking,
    defaultLanguageCodes,
    effectiveMetadataProvider,
    effectiveSearchMode,
    runSearchWithPolicyRefresh,
    setAdvancedFilters,
    setSearchInput,
    seriesBrowseCapability?.sort,
    seriesBrowseTarget,
    updateSearchFieldValue,
  ]);

  const canSearchSeriesForBook = useCallback((book: Book | null): boolean => {
    if (!book?.provider || !book.series_name) {
      return false;
    }

    if (!seriesBrowseCapability?.sort || !seriesBrowseTarget?.field || !activeMetadataConfig?.provider) {
      return false;
    }

    return book.provider === activeMetadataConfig.provider;
  }, [
    activeMetadataConfig?.provider,
    seriesBrowseCapability?.sort,
    seriesBrowseTarget?.field,
  ]);

  const handleManualSearch = useCallback(() => {
    const trimmed = searchInput.trim();
    if (!trimmed) return;
    const manualId = `manual_${Date.now()}`;
    const syntheticBook: Book = {
      id: manualId,
      title: trimmed,
      author: '',
      provider: 'manual',
      provider_id: manualId,
      search_title: trimmed,
    };
    setReleaseBook(syntheticBook);
  }, [searchInput]);

  useEffect(() => {
    if (!manualSearchAllowed && activeQueryTarget === 'manual') {
      setActiveQueryTarget(getDefaultQueryTargetKey(queryTargets));
    }
  }, [manualSearchAllowed, activeQueryTarget, queryTargets]);

  // Unified search dispatch: intercepts manual search mode, otherwise runs normal search
  const handleSearchDispatch = useCallback(() => {
    if (activeQueryOption?.source === 'manual') {
      handleManualSearch();
      return;
    }
    const request = buildCurrentSearchRequest();
    const shouldPersistAppliedSort = !(
      effectiveSearchMode === 'universal'
      && activeQueryUsesSeriesBrowse
      && request.appliedSort === seriesBrowseCapability?.sort
    );

    if (shouldPersistAppliedSort && request.appliedSort !== advancedFilters.sort) {
      updateAdvancedFilters({ sort: request.appliedSort });
    }
    setActiveResultsSort(request.appliedSort);
    runSearchWithPolicyRefresh({
      query: request.query,
      fieldValues: request.fieldValues,
      searchModeOverride: effectiveSearchMode,
      providerOverride: request.providerOverride,
    });
  }, [
    activeQueryOption,
    advancedFilters.sort,
    activeQueryUsesSeriesBrowse,
    buildCurrentSearchRequest,
    effectiveSearchMode,
    handleManualSearch,
    runSearchWithPolicyRefresh,
    seriesBrowseCapability?.sort,
    updateAdvancedFilters,
  ]);

  const isBrowseFulfilMode = fulfillingRequest !== null;
  const activeReleaseBook = fulfillingRequest?.book ?? releaseBook;
  const activeReleaseContentType = fulfillingRequest?.contentType ?? contentType;
  const usePinnedMainScrollContainer = sidebarPinnedOpen;

  const handleReleaseModalClose = useCallback(() => {
    if (isBrowseFulfilMode) {
      setFulfillingRequest(null);
      return;
    }
    setReleaseBook(null);
  }, [isBrowseFulfilMode]);

  const pendingOnBehalfTitle = pendingOnBehalfDownload
    ? pendingOnBehalfDownload.type === 'book'
      ? pendingOnBehalfDownload.book.title || 'Untitled'
      : pendingOnBehalfDownload.release.title ||
        pendingOnBehalfDownload.book.title ||
        'Untitled'
    : '';
  const pendingOnBehalfUserName = pendingOnBehalfDownload
    ? formatActingAsUserName(pendingOnBehalfDownload.actingAsUser)
    : '';

  const mainAppContent = (
    <SearchModeProvider searchMode={effectiveSearchMode}>
      <div ref={headerRef} className="fixed top-0 left-0 right-0 z-40">
        <Header
          calibreWebUrl={config?.calibre_web_url || ''}
          audiobookLibraryUrl={config?.audiobook_library_url || ''}
          debug={config?.debug || false}
          logoUrl={logoUrl}
          showSearch={!isInitialState}
          searchInput={activeQueryValue}
          searchInputLabel={activeQueryValueLabel}
          onSearchChange={handleActiveQueryValueChange}
          onDownloadsClick={() => setDownloadsSidebarOpen((prev) => !prev)}
          onSettingsClick={() => {
            if (config?.settings_enabled) {
              if (authIsAdmin) {
                setSettingsOpen(true);
              } else {
                setSelfSettingsOpen(true);
              }
            } else {
              setConfigBannerOpen(true);
            }
          }}
          isAdmin={requestRoleIsAdmin}
          canAccessSettings={isAuthenticated}
          username={username}
          displayName={displayName}
          actingAsUser={actingAsUser}
          onActingAsUserChange={setActingAsUser}
          statusCounts={statusCounts}
          onLogoClick={() => {
            handleResetSearch(config);
            setActiveQueryTarget('general');
            setActiveResultsSort('');
          }}
          authRequired={authRequired}
          isAuthenticated={isAuthenticated}
          onLogout={handleLogoutWithCleanup}
          onSearch={handleSearchDispatch}
          onAdvancedToggle={hasAdvancedContent ? () => setShowAdvanced(!showAdvanced) : undefined}
          isLoading={isSearching}
          onShowToast={showToast}
          onRemoveToast={removeToast}
          contentType={contentType}
          onContentTypeChange={setContentType}
          allowedContentTypes={allowedContentTypes}
          queryTargets={queryTargets}
          activeQueryTarget={activeQueryTarget}
          onQueryTargetChange={setActiveQueryTarget}
          activeQueryField={activeQueryField}
        />
      </div>

      <div
        className={`flex flex-col${
          usePinnedMainScrollContainer
            ? ' min-h-0 overflow-y-auto overscroll-y-contain'
            : ' flex-1'
        }`}
        style={
          usePinnedMainScrollContainer
            ? {
                position: 'fixed',
                top: `${headerHeight}px`,
                bottom: 0,
                left: 0,
                right: '25rem',
                zIndex: 40,
              }
            : { paddingTop: `${headerHeight}px` }
        }
      >
        <AdvancedFilters
          visible={showAdvanced && !isInitialState}
          bookLanguages={bookLanguages}
          defaultLanguage={defaultLanguageCodes}
          filters={advancedFilters}
          onFiltersChange={updateAdvancedFilters}
          searchMode={effectiveSearchMode}
          onSearchModeChange={handleSearchModeChange}
          metadataProviders={metadataProviders}
          activeMetadataProvider={effectiveMetadataProvider}
          onMetadataProviderChange={handleMetadataProviderChange}
          contentType={contentType}
          isAdmin={requestRoleIsAdmin}
        />

      <main
        className="relative w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3 sm:py-6"
        style={
          usePinnedMainScrollContainer
            ? { display: 'block', flex: '0 0 auto', minHeight: 0 }
            : undefined
        }
      >
        <SearchSection
          onSearch={handleSearchDispatch}
          isLoading={isSearching}
          isInitialState={isInitialState}
          bookLanguages={bookLanguages}
          defaultLanguage={defaultLanguageCodes}
          logoUrl={logoUrl}
          queryValue={activeQueryValue}
          queryValueLabel={activeQueryValueLabel}
          onQueryValueChange={handleActiveQueryValueChange}
          queryTargets={queryTargets}
          activeQueryTarget={activeQueryTarget}
          onQueryTargetChange={setActiveQueryTarget}
          showAdvanced={showAdvanced}
          onAdvancedToggle={hasAdvancedContent ? () => setShowAdvanced(!showAdvanced) : undefined}
          advancedFilters={advancedFilters}
          onAdvancedFiltersChange={updateAdvancedFilters}
          contentType={contentType}
          onContentTypeChange={setContentType}
          allowedContentTypes={allowedContentTypes}
          activeQueryField={activeQueryField}
          searchMode={effectiveSearchMode}
          onSearchModeChange={handleSearchModeChange}
          metadataProviders={metadataProviders}
          activeMetadataProvider={effectiveMetadataProvider}
          onMetadataProviderChange={handleMetadataProviderChange}
          isAdmin={requestRoleIsAdmin}
        />

        <ResultsSection
          books={books}
          visible={hasResults}
          onDetails={handleShowDetails}
          onDownload={handleDownload}
          onGetReleases={handleGetReleases}
          getButtonState={getDirectActionButtonState}
          getUniversalButtonState={getUniversalActionButtonState}
          sortValue={visibleResultsSort}
          showSortControl={!activeQueryUsesSeriesBrowse && !activeQueryUsesListBrowse}
          onSortChange={(value) => {
            const request = buildCurrentSearchRequest(value);
            const shouldPersistAppliedSort = !(
              effectiveSearchMode === 'universal'
              && activeQueryUsesSeriesBrowse
              && request.appliedSort === seriesBrowseCapability?.sort
            );
            if (shouldPersistAppliedSort) {
              updateAdvancedFilters({ sort: request.appliedSort });
            }
            setActiveResultsSort(request.appliedSort);
            runSearchWithPolicyRefresh({
              query: request.query,
              fieldValues: request.fieldValues,
              searchModeOverride: effectiveSearchMode,
              providerOverride: request.providerOverride,
            });
          }}
          metadataSortOptions={resolvedMetadataSortOptions}
          hasMore={hasMore}
          isLoadingMore={isLoadingMore}
          onLoadMore={() => loadMore(config, effectiveSearchMode)}
          totalFound={totalFound}
          onShowToast={showToast}
          resultsSourceUrl={resultsSourceUrl}
        />

        {selectedBook && (
          <DetailsModal
            book={selectedBook}
            onClose={() => setSelectedBook(null)}
            onDownload={handleDownload}
            onShowToast={showToast}
            onFindDownloads={(book) => {
              setSelectedBook(null);
              void handleGetReleases(book);
            }}
            onSearchSeries={canSearchSeriesForBook(selectedBook) ? handleSearchSeries : undefined}
            buttonState={
              isMetadataBook(selectedBook)
                ? getUniversalActionButtonState(selectedBook.id)
                : getDirectActionButtonState(selectedBook.id)
            }
          />
        )}

        {activeReleaseBook && (
          <ReleaseModal
            book={activeReleaseBook}
            onClose={handleReleaseModalClose}
            onDownload={isBrowseFulfilMode ? handleBrowseFulfilDownload : handleReleaseDownload}
            onRequestRelease={isBrowseFulfilMode ? undefined : handleReleaseRequest}
            onRequestBook={
              isBrowseFulfilMode || !requestRoleIsAdmin
                ? undefined
                : handleReleaseBookRequest
            }
            getPolicyModeForSource={isBrowseFulfilMode ? () => 'download' : (source, ct) => getSourceMode(source, ct)}
            onPolicyRefresh={handleReleaseModalPolicyRefresh}
            supportedFormats={supportedFormats}
            supportedAudiobookFormats={config?.supported_audiobook_formats || []}
            contentType={activeReleaseContentType}
            defaultLanguages={defaultLanguageCodes}
            bookLanguages={bookLanguages}
            currentStatus={statusForButtonState}
            defaultReleaseSource={config?.default_release_source}
            onSearchSeries={isBrowseFulfilMode || !canSearchSeriesForBook(activeReleaseBook) ? undefined : handleSearchSeries}
            defaultShowManualQuery={isBrowseFulfilMode || activeReleaseBook?.provider === 'manual'}
            isRequestMode={isBrowseFulfilMode || activeReleaseBook?.provider === 'manual'}
            onShowToast={showToast}
          />
        )}

        {pendingRequestPayload && (
          <RequestConfirmationModal
            payload={pendingRequestPayload}
            allowNotes={allowRequestNotes}
            onConfirm={handleConfirmRequest}
            onClose={() => setPendingRequestPayload(null)}
          />
        )}

        {pendingOnBehalfDownload && (
          <OnBehalfConfirmationModal
            isOpen={Boolean(pendingOnBehalfDownload)}
            actingAsName={pendingOnBehalfUserName}
            itemTitle={pendingOnBehalfTitle}
            onConfirm={handleConfirmOnBehalfDownload}
            onClose={() => setPendingOnBehalfDownload(null)}
          />
        )}

      </main>

      <div className={usePinnedMainScrollContainer ? 'mt-auto' : undefined}>
        <Footer
          buildVersion={config?.build_version}
          releaseVersion={config?.release_version}
          debug={config?.debug}
        />
      </div>
      </div>

      <ActivitySidebar
        isOpen={downloadsSidebarOpen}
        onClose={() => setDownloadsSidebarOpen(false)}
        status={activitySidebarStatus}
        isAdmin={requestRoleIsAdmin}
        onClearCompleted={handleClearCompleted}
        onCancel={handleCancel}
        onRetry={handleRetry}
        onDownloadDismiss={handleDownloadDismiss}
        requestItems={requestItems}
        dismissedItemKeys={dismissedActivityKeys}
        historyItems={historyItems}
        historyLoaded={activityHistoryLoaded}
        historyHasMore={activityHistoryHasMore}
        historyLoading={activityHistoryLoading}
        onHistoryLoadMore={handleActivityHistoryLoadMore}
        onClearHistory={handleClearHistory}
        onActiveTabChange={handleActivityTabChange}
        pendingRequestCount={pendingRequestCount}
        showRequestsTab={showRequestsTab}
        isRequestsLoading={isRequestsLoading || isActivitySnapshotLoading}
        onRequestCancel={showRequestsTab ? handleRequestCancel : undefined}
        onRequestApprove={requestRoleIsAdmin ? handleRequestApprove : undefined}
        onRequestReject={requestRoleIsAdmin ? handleRequestReject : undefined}
        onRequestDismiss={showRequestsTab ? handleRequestDismiss : undefined}
        onPinnedOpenChange={setSidebarPinnedOpen}
        pinnedTopOffset={headerHeight}
      />

      <ToastContainer toasts={toasts} />

      <SettingsModal
        isOpen={settingsOpen}
        authMode={authMode}
        onClose={() => setSettingsOpen(false)}
        onShowToast={showToast}
        onSettingsSaved={handleSettingsSaved}
        onRefreshAuth={refreshAuth}
      />

      <SelfSettingsModal
        isOpen={selfSettingsOpen}
        onClose={() => setSelfSettingsOpen(false)}
        onShowToast={showToast}
        onSettingsSaved={handleSettingsSaved}
      />

      {/* Auto-show banner on startup for users without config */}
      {config && (
        <ConfigSetupBanner settingsEnabled={config.settings_enabled} />
      )}

      {/* Controlled banner shown when clicking settings without config */}
      <ConfigSetupBanner
        isOpen={configBannerOpen}
        onClose={() => setConfigBannerOpen(false)}
        onContinue={() => {
          setConfigBannerOpen(false);
          if (authIsAdmin) {
            setSettingsOpen(true);
          } else {
            setSelfSettingsOpen(true);
          }
        }}
      />

      {/* Onboarding wizard shown on first run */}
      <OnboardingModal
        isOpen={onboardingOpen}
        onClose={() => setOnboardingOpen(false)}
        onComplete={() => loadConfig('settings-saved')}
        onShowToast={showToast}
      />

    </SearchModeProvider>
  );

  const visuallyHiddenStyle: CSSProperties = {
    position: 'absolute',
    width: '1px',
    height: '1px',
    padding: 0,
    margin: '-1px',
    overflow: 'hidden',
    clip: 'rect(0, 0, 0, 0)',
    whiteSpace: 'nowrap',
    border: 0,
  };

  if (!authChecked) {
    return (
      <div aria-live="polite" style={visuallyHiddenStyle}>
        Checking authentication…
      </div>
    );
  }

  // Wait for config to load before rendering main UI to prevent flicker
  if (isAuthenticated && !config) {
    return (
      <div aria-live="polite" style={visuallyHiddenStyle}>
        Loading configuration…
      </div>
    );
  }

  const shouldRedirectFromLogin = !authRequired || isAuthenticated;
  const appElement = authRequired && !isAuthenticated ? (
    <Navigate to="/login" replace />
  ) : (
    mainAppContent
  );

  return (
    <Routes>
      <Route
        path="/login"
        element={
          shouldRedirectFromLogin ? (
            <Navigate to="/" replace />
          ) : (
            <LoginPage
              onLogin={handleLogin}
              error={loginError}
              isLoading={isLoggingIn}
              authMode={authMode}
              oidcButtonLabel={oidcButtonLabel}
              hideLocalAuth={hideLocalAuth}
              oidcAutoRedirect={oidcAutoRedirect}
            />
          )
        }
      />
      <Route path="/*" element={appElement} />
    </Routes>
  );
}

export default App;
