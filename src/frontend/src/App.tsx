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
  isMetadataBook,
} from './types';
import {
  getBookInfo,
  getMetadataBookInfo,
  downloadBook,
  downloadRelease,
  cancelDownload,
  retryDownload,
  getConfig,
  createRequest,
  isApiResponseError,
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
import {
  applyDirectPolicyModeToButtonState,
  applyUniversalPolicyModeToButtonState,
} from './utils/requestPolicyUi';
import {
  buildDirectRequestPayload,
  buildMetadataBookRequestData,
  buildReleaseDataFromMetadataRelease,
  getRequestSuccessMessage,
  toContentType,
} from './utils/requestPayload';
import { bookFromRequestData } from './utils/requestFulfil';
import { policyTrace } from './utils/policyTrace';
import { SearchModeProvider } from './contexts/SearchModeContext';
import { useSocket } from './contexts/SocketContext';
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
    requestItems,
    dismissedActivityKeys,
    historyItems,
    pendingRequestCount,
    isActivitySnapshotLoading,
    activityHistoryLoading,
    activityHistoryHasMore,
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
    handleSortChange,
    searchFieldValues,
    updateSearchFieldValue,
    // Pagination (universal mode)
    hasMore,
    isLoadingMore,
    loadMore,
    totalFound,
  } = useSearch({
    showToast,
    setIsAuthenticated,
    authRequired,
    onSearchReset: clearTracking,
    contentType,
  });

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
  const [downloadsSidebarOpen, setDownloadsSidebarOpen] = useState(false);
  const [sidebarPinnedOpen, setSidebarPinnedOpen] = useState(false);
  const [headerHeight, setHeaderHeight] = useState(0);
  const headerObserverRef = useRef<ResizeObserver | null>(null);
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
      currentStatus.queued,
      currentStatus.resolving,
      currentStatus.locating,
      currentStatus.downloading,
    ].reduce((sum, status) => sum + countVisibleDownloads(status, { filterDismissed: false }), 0);

    const completed = countVisibleDownloads(currentStatus.complete, { filterDismissed: true });
    const errored = countVisibleDownloads(currentStatus.error, { filterDismissed: true });
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
  }, [currentStatus, dismissedActivityKeys, requestItems]);


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
      const cfg = await getConfig();

      // Check if search mode changed (only on settings save)
      if (mode === 'settings-saved' && prevSearchModeRef.current !== cfg.search_mode) {
        setBooks([]);
        setSelectedBook(null);
        clearTracking();
      }

      prevSearchModeRef.current = cfg.search_mode;
      setConfig(cfg);

      // Show onboarding modal on first run (settings enabled but not completed yet)
      if (mode === 'initial' && cfg.settings_enabled && !cfg.onboarding_complete) {
        setOnboardingOpen(true);
      }

      // Determine the default sort based on search mode
      const defaultSort = cfg.search_mode === 'universal'
        ? (cfg.metadata_default_sort || 'relevance')
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
  }, [setBooks, setAdvancedFilters, clearTracking]);

  // Fetch config when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      loadConfig('initial');
    }
  }, [isAuthenticated, loadConfig]);

  const runSearchWithPolicyRefresh = useCallback(
    (query: string, fields = searchFieldValues) => {
      void refreshRequestPolicy();
      handleSearch(query, config, fields);
    },
    [refreshRequestPolicy, handleSearch, config, searchFieldValues]
  );

  // Execute URL-based search when params are present
  useEffect(() => {
    if (
      wasProcessed &&
      parsedParams?.hasSearchParams &&
      !urlSearchExecutedRef.current &&
      config
    ) {
      urlSearchExecutedRef.current = true;

      const searchMode = config.search_mode || 'direct';
      const bookLanguages = config.book_languages || [];
      const defaultLanguageCodes =
        config.default_language && config.default_language.length > 0
          ? config.default_language
          : [bookLanguages[0]?.code || 'en'];

      // Populate search input from URL
      if (parsedParams.searchInput) {
        setSearchInput(parsedParams.searchInput);
      }

      // Apply advanced filters from URL
      if (Object.keys(parsedParams.advancedFilters).length > 0) {
        setAdvancedFilters(prev => ({
          ...prev,
          ...parsedParams.advancedFilters,
        }));

        // Show advanced panel if we have filter values (not just query/sort)
        const hasAdvancedValues = ['isbn', 'author', 'title', 'content'].some(
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
      };

      const query = buildSearchQuery({
        searchInput: parsedParams.searchInput,
        showAdvanced: true,
        advancedFilters: mergedFilters as typeof advancedFilters,
        bookLanguages,
        defaultLanguage: defaultLanguageCodes,
        searchMode,
      });

      runSearchWithPolicyRefresh(query);
    }
  }, [
    wasProcessed,
    parsedParams,
    config,
    advancedFilters,
    searchFieldValues,
    runSearchWithPolicyRefresh,
    setSearchInput,
    setAdvancedFilters,
    setShowAdvanced,
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
    const metadataBook = books.find(b => b.id === id && b.provider && b.provider_id);

    if (metadataBook) {
      try {
        const fullBook = await getMetadataBookInfo(metadataBook.provider!, metadataBook.provider_id!);
        setSelectedBook({
          ...metadataBook,
          description: fullBook.description || metadataBook.description,
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
        const book = await getBookInfo(id);
        setSelectedBook(book);
      } catch (error) {
        console.error('Failed to load book details:', error);
        showToast('Failed to load book details', 'error');
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

  const getDirectPolicyMode = useCallback((): RequestPolicyMode => {
    return getSourceMode('direct_download', 'ebook');
  }, [getSourceMode]);

  const getUniversalDefaultPolicyMode = useCallback((): RequestPolicyMode => {
    return getDefaultMode(contentType);
  }, [getDefaultMode, contentType]);

  const buildReleaseDownloadPayload = useCallback(
    (book: Book, release: Release, releaseContentType: ContentType): DownloadReleasePayload => ({
      source: release.source,
      source_id: release.source_id,
      title: book.title,    // Use book metadata title, not release/torrent title
      author: book.author,  // Pass author from metadata
      year: book.year,      // Pass year from metadata
      format: release.format,
      size: release.size,
      size_bytes: release.size_bytes,
      download_url: release.download_url,
      protocol: release.protocol,
      indexer: release.indexer,
      seeders: release.seeders,
      extra: release.extra,
      preview: book.preview,  // Pass book cover from metadata
      content_type: releaseContentType,  // For audiobook directory routing
      series_name: book.series_name,
      series_position: book.series_position,
      subtitle: book.subtitle,
    }),
    []
  );

  const executeBookDownload = useCallback(
    async (book: Book, onBehalfOfUserId?: number): Promise<void> => {
      try {
        await downloadBook(book.id, onBehalfOfUserId);
        await fetchStatus();
      } catch (error) {
        console.error('Download failed:', error);
        if (isPolicyGuardError(error)) {
          const requiredMode = getPolicyGuardRequiredMode(error);
          policyTrace('direct.action:policy_guard', {
            bookId: book.id,
            requiredMode,
            code: isApiResponseError(error) ? error.code : null,
          });
          if (requiredMode === 'request_release' || requiredMode === 'request_book') {
            openRequestConfirmation(buildDirectRequestPayload(book, requiredMode));
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
    [fetchStatus, openRequestConfirmation, refreshRequestPolicy, showToast]
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
                source: release.source || 'direct_download',
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
                source: release.source || 'direct_download',
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
    [buildReleaseDownloadPayload, fetchStatus, openRequestConfirmation, refreshRequestPolicy, showToast, trackRelease]
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
    let mode = getDirectPolicyMode();
    policyTrace('direct.action:start', {
      bookId: book.id,
      contentType: 'ebook',
      cachedMode: mode,
      isAdmin: requestRoleIsAdmin,
    });
    try {
      const latestPolicy = await refreshRequestPolicy({ force: true });
      const effectiveIsAdmin = latestPolicy ? Boolean(latestPolicy.is_admin) : requestRoleIsAdmin;
      mode = resolveSourceModeFromPolicy(latestPolicy, effectiveIsAdmin, 'direct_download', 'ebook');
      policyTrace('direct.action:resolved', {
        bookId: book.id,
        resolvedMode: mode,
        effectiveIsAdmin,
        defaults: latestPolicy?.defaults ?? null,
        requestsEnabled: latestPolicy?.requests_enabled ?? null,
      });
    } catch (error) {
      console.warn('Failed to refresh request policy before direct action:', error);
      policyTrace('direct.action:refresh_failed', {
        bookId: book.id,
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

    if (mode === 'request_release' || mode === 'request_book') {
      policyTrace('direct.action:request_modal', { bookId: book.id, mode });
      openRequestConfirmation(buildDirectRequestPayload(book, mode));
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
          source: release.source || 'direct_download',
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
      if (baseState.state === 'complete' && isDownloadTaskDismissed(bookId)) {
        return applyDirectPolicyModeToButtonState(
          { text: 'Download', state: 'download' },
          getDirectPolicyMode()
        );
      }
      const mode = getDirectPolicyMode();
      return applyDirectPolicyModeToButtonState(baseState, mode);
    },
    [getButtonState, getDirectPolicyMode, isDownloadTaskDismissed]
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

  const searchMode = config?.search_mode || 'direct';
  const logoUrl = withBasePath('/logo.png');

  // Handle "View Series" - trigger search with series field and series order sort
  const handleSearchSeries = useCallback((seriesName: string) => {
    // Clear UI state
    setSearchInput('');
    setSelectedBook(null);
    setReleaseBook(null);
    clearTracking();

    // Set sort to series_order (but don't show advanced panel or persist series value)
    const newFilters = { ...advancedFilters, sort: 'series_order' };
    setAdvancedFilters(newFilters);

    // Trigger search with series field (passed directly, not persisted in UI)
    const query = buildSearchQuery({
      searchInput: '',
      showAdvanced: true,
      advancedFilters: newFilters,
      bookLanguages,
      defaultLanguage: defaultLanguageCodes,
      searchMode,
    });
    runSearchWithPolicyRefresh(query, { ...searchFieldValues, series: seriesName });
  }, [setSearchInput, clearTracking, searchFieldValues, advancedFilters, setAdvancedFilters, bookLanguages, defaultLanguageCodes, searchMode, runSearchWithPolicyRefresh]);

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
    <SearchModeProvider searchMode={searchMode}>
      <div ref={headerRef} className="fixed top-0 left-0 right-0 z-40">
        <Header
          calibreWebUrl={config?.calibre_web_url || ''}
          audiobookLibraryUrl={config?.audiobook_library_url || ''}
          debug={config?.debug || false}
          logoUrl={logoUrl}
          showSearch={!isInitialState}
          searchInput={searchInput}
          onSearchChange={setSearchInput}
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
          onLogoClick={() => handleResetSearch(config)}
          authRequired={authRequired}
          isAuthenticated={isAuthenticated}
          onLogout={handleLogoutWithCleanup}
          onSearch={() => {
            const query = buildSearchQuery({
              searchInput,
              showAdvanced,
              advancedFilters,
              bookLanguages,
              defaultLanguage: defaultLanguageCodes,
              searchMode,
            });
            runSearchWithPolicyRefresh(query);
          }}
          onAdvancedToggle={() => setShowAdvanced(!showAdvanced)}
          isLoading={isSearching}
          onShowToast={showToast}
          onRemoveToast={removeToast}
          contentType={contentType}
          onContentTypeChange={setContentType}
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
        supportedFormats={supportedFormats}
        filters={advancedFilters}
        onFiltersChange={updateAdvancedFilters}
        metadataSearchFields={config?.metadata_search_fields}
        searchFieldValues={searchFieldValues}
        onSearchFieldChange={updateSearchFieldValue}
        onSubmit={() => {
          const query = buildSearchQuery({
            searchInput,
            showAdvanced,
            advancedFilters,
            bookLanguages,
            defaultLanguage: defaultLanguageCodes,
            searchMode,
          });
          runSearchWithPolicyRefresh(query);
        }}
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
          onSearch={(query) => runSearchWithPolicyRefresh(query)}
          isLoading={isSearching}
          isInitialState={isInitialState}
          bookLanguages={bookLanguages}
          defaultLanguage={defaultLanguageCodes}
          supportedFormats={config?.supported_formats || DEFAULT_SUPPORTED_FORMATS}
          logoUrl={logoUrl}
          searchInput={searchInput}
          onSearchInputChange={setSearchInput}
          showAdvanced={showAdvanced}
          onAdvancedToggle={() => setShowAdvanced(!showAdvanced)}
          advancedFilters={advancedFilters}
          onAdvancedFiltersChange={updateAdvancedFilters}
          metadataSearchFields={config?.metadata_search_fields}
          searchFieldValues={searchFieldValues}
          onSearchFieldChange={updateSearchFieldValue}
          contentType={contentType}
          onContentTypeChange={setContentType}
        />

        <ResultsSection
          books={books}
          visible={hasResults}
          onDetails={handleShowDetails}
          onDownload={handleDownload}
          onGetReleases={handleGetReleases}
          getButtonState={getDirectActionButtonState}
          getUniversalButtonState={getUniversalActionButtonState}
          sortValue={advancedFilters.sort}
          onSortChange={(value) => handleSortChange(value, config)}
          metadataSortOptions={config?.metadata_sort_options}
          hasMore={hasMore}
          isLoadingMore={isLoadingMore}
          onLoadMore={() => loadMore(config)}
          totalFound={totalFound}
        />

        {selectedBook && (
          <DetailsModal
            book={selectedBook}
            onClose={() => setSelectedBook(null)}
            onDownload={handleDownload}
            onFindDownloads={(book) => {
              setSelectedBook(null);
              void handleGetReleases(book);
            }}
            onSearchSeries={handleSearchSeries}
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
            onSearchSeries={isBrowseFulfilMode ? undefined : handleSearchSeries}
            defaultShowManualQuery={isBrowseFulfilMode}
            isRequestMode={isBrowseFulfilMode}
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
        status={currentStatus}
        isAdmin={requestRoleIsAdmin}
        onClearCompleted={handleClearCompleted}
        onCancel={handleCancel}
        onRetry={handleRetry}
        onDownloadDismiss={handleDownloadDismiss}
        requestItems={requestItems}
        dismissedItemKeys={dismissedActivityKeys}
        historyItems={historyItems}
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
