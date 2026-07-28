import { useCallback, useMemo, useRef, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { ActivitySidebar } from './components/activity';
import { ConfigSetupBanner } from './components/ConfigSetupBanner';
import { Footer } from './components/Footer';
import { Header } from './components/Header';
import { OnboardingModal } from './components/OnboardingModal';
import { ReleaseModal } from './components/ReleaseModal';
import { SelfSettingsModal, SettingsModal } from './components/settings';
import { ToastContainer } from './components/ToastContainer';
import { useSocket } from './contexts/SocketContext';
import { DEFAULT_LANGUAGES, DEFAULT_SUPPORTED_FORMATS } from './data/languages';
import { useContentTypePreferences } from './hooks/app/useContentTypePreferences';
import { useShowOnboardingDebug } from './hooks/app/useShowOnboardingDebug';
import { useStatusChangeNotifications } from './hooks/app/useStatusChangeNotifications';
import { useActivity } from './hooks/useActivity';
import { useAuth } from './hooks/useAuth';
import { useDownloadTracking } from './hooks/useDownloadTracking';
import { useMediaQuery } from './hooks/useMediaQuery';
import { useDependencyEffect } from './hooks/useMountEffect';
import { useRealtimeStatus } from './hooks/useRealtimeStatus';
import { useRequests } from './hooks/useRequests';
import { primeSettingsCache } from './hooks/useSettings';
import { useToast } from './hooks/useToast';
import { primeUsersCache } from './hooks/useUsersFetch';
import { BookDetailPage } from './library/BookDetailPage';
import { LibraryNavigation } from './library/LibraryNavigation';
import { LibraryPage } from './library/LibraryPage';
import { SearchPage } from './library/SearchPage';
import { LoginPage } from './pages/LoginPage';
import {
  cancelDownload,
  downloadRelease,
  getConfig,
  retryDownload,
  type DownloadReleasePayload,
} from './services/api';
import type { AppConfig, Book, ContentType, Release, RequestRecord, StatusData } from './types';
import { buildLoginRedirectPath, getReturnToFromSearch } from './utils/authRedirect';
import { canUseManualReleaseQuery, isRequestOnlyLibraryUser } from './utils/releaseCapabilities';
import { buildReleaseDataFromMetadataRelease } from './utils/releasePayload';
import { bookFromRequestRecord } from './utils/requestFulfil';

// oxlint-disable-next-line import/no-unassigned-import
import './styles.css';

const ACTIVITY_SIDEBAR_PINNED_STORAGE_KEY = 'activity-sidebar-pinned';

const getInitialPinnedPreference = (): boolean => {
  try {
    const value = window.localStorage.getItem(ACTIVITY_SIDEBAR_PINNED_STORAGE_KEY);
    return value === '1' || value?.toLowerCase() === 'true';
  } catch {
    return false;
  }
};

const getErrorMessage = (error: unknown, fallback: string): string =>
  error instanceof Error && error.message ? error.message : fallback;

function App() {
  const location = useLocation();
  const { toasts, showToast, removeToast } = useToast();
  const { socket } = useSocket();
  const { status: currentStatus, forceRefresh: fetchStatus } = useRealtimeStatus({
    pollInterval: 5000,
  });
  const { bookToReleaseMap, trackRelease, markBookCompleted } = useDownloadTracking(currentStatus);
  const {
    isAuthenticated,
    authRequired,
    authChecked,
    isAdmin,
    libraryCapability,
    authMode,
    username,
    displayName,
    oidcButtonLabel,
    hideLocalAuth,
    oidcAutoRedirect,
    loginError,
    isLoggingIn,
    refreshAuth,
    handleLogin,
    handleLogout,
  } = useAuth({ showToast });
  const { contentType } = useContentTypePreferences();
  const { cancelRequest, fulfilBookRequests, rejectRequest } = useRequests({ isAdmin });
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
  } = useActivity({ isAuthenticated, isAdmin, showToast, socket });

  const [config, setConfig] = useState<AppConfig | null>(null);
  const [releaseBook, setReleaseBook] = useState<Book | null>(null);
  const [fulfillingRequest, setFulfillingRequest] = useState<{
    requestId: number;
    book: Book;
    contentType: ContentType;
  } | null>(null);
  const [downloadsSidebarOpen, setDownloadsSidebarOpen] = useState(false);
  const [libraryNavigationOpen, setLibraryNavigationOpen] = useState(false);
  const [sidebarPinnedOpen, setSidebarPinnedOpen] = useState(getInitialPinnedPreference);
  const [headerHeight, setHeaderHeight] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [selfSettingsOpen, setSelfSettingsOpen] = useState(false);
  const [configBannerOpen, setConfigBannerOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const headerObserverRef = useRef<ResizeObserver | null>(null);
  const isDesktopViewport = useMediaQuery('(min-width: 1024px)');

  useShowOnboardingDebug({ setOnboardingOpen });

  const loadConfig = useCallback(async () => {
    try {
      const nextConfig = await getConfig();
      setConfig(nextConfig);
      if (nextConfig.settings_enabled && !nextConfig.onboarding_complete) {
        setOnboardingOpen(true);
      }
    } catch (error) {
      console.error('Failed to load config:', error);
    }
  }, []);

  useDependencyEffect(() => {
    if (!isAuthenticated) return;
    void fetchStatus();
    void refreshActivitySnapshot();
    void loadConfig();
  }, [fetchStatus, isAuthenticated, loadConfig, refreshActivitySnapshot]);

  useStatusChangeNotifications({
    currentStatus,
    config,
    showToast,
    openDownloadsSidebar: () => setDownloadsSidebarOpen(true),
    bookToReleaseMap,
    markBookCompleted,
  });

  const dismissedDownloadTaskIds = useMemo(
    () =>
      new Set(
        dismissedActivityKeys
          .filter((key) => key.startsWith('download:'))
          .map((key) => key.slice('download:'.length)),
      ),
    [dismissedActivityKeys],
  );
  const activitySidebarStatus = useMemo<StatusData>(() => {
    const retained = (bucket: Record<string, Book> | undefined) =>
      bucket
        ? (Object.fromEntries(
            Object.entries(bucket).filter(([id]) => !dismissedDownloadTaskIds.has(id)),
          ) as Record<string, Book>)
        : undefined;
    return {
      queued: currentStatus.queued,
      resolving: currentStatus.resolving,
      locating: currentStatus.locating,
      downloading: currentStatus.downloading,
      complete: retained(activityStatus.complete),
      error: retained(activityStatus.error),
      cancelled: retained(activityStatus.cancelled),
    };
  }, [activityStatus, currentStatus, dismissedDownloadTaskIds]);
  const statusCounts = useMemo(() => {
    const count = (bucket: Record<string, Book> | undefined) => Object.keys(bucket ?? {}).length;
    return {
      ongoing:
        count(currentStatus.queued) +
        count(currentStatus.resolving) +
        count(currentStatus.locating) +
        count(currentStatus.downloading),
      completed: count(activitySidebarStatus.complete),
      errored: count(activitySidebarStatus.error),
      pendingRequests: requestItems.filter((item) => item.requestRecord?.status === 'pending')
        .length,
    };
  }, [activitySidebarStatus, currentStatus, requestItems]);

  const headerRef = useCallback((element: HTMLDivElement | null) => {
    headerObserverRef.current?.disconnect();
    if (!element) return;
    const measure = () => setHeaderHeight(element.getBoundingClientRect().height);
    measure();
    headerObserverRef.current = new ResizeObserver(measure);
    headerObserverRef.current.observe(element);
  }, []);

  const handleSettingsClick = useCallback(() => {
    if (!config?.settings_enabled) {
      setConfigBannerOpen(true);
    } else if (isAdmin) {
      void primeUsersCache();
      void primeSettingsCache();
      setSettingsOpen(true);
    } else {
      setSelfSettingsOpen(true);
    }
  }, [config?.settings_enabled, isAdmin]);

  const handlePersonalSettingsClick = useCallback(() => {
    setSelfSettingsOpen(true);
  }, []);

  const handleReleaseDownload = useCallback(
    async (book: Book, release: Release, releaseContentType: ContentType) => {
      const payload: DownloadReleasePayload = {
        source: release.source,
        source_id: release.source_id,
        title: release.title,
        author: book.author,
        year: book.year,
        format: release.format,
        size: release.size,
        size_bytes: release.size_bytes,
        download_url: release.download_url,
        protocol: release.protocol,
        indexer: release.indexer,
        seeders: release.seeders,
        extra: release.extra,
        preview: book.preview,
        content_type: releaseContentType,
        series_name: book.series_name,
        series_position: book.series_position,
        subtitle: book.subtitle,
        library_book_id: book.book_id ?? undefined,
        search_author: book.search_author,
      };
      try {
        trackRelease(book.id, release.source_id);
        await downloadRelease(payload);
        await fetchStatus();
      } catch (error) {
        showToast(getErrorMessage(error, 'Failed to queue download'), 'error');
        throw error;
      }
    },
    [fetchStatus, showToast, trackRelease],
  );

  const handleRequestCancel = useCallback(
    async (requestId: number) => {
      try {
        await cancelRequest(requestId);
        await refreshActivitySnapshot();
        showToast('Request cancelled', 'success');
      } catch (error) {
        showToast(getErrorMessage(error, 'Failed to cancel request'), 'error');
      }
    },
    [cancelRequest, refreshActivitySnapshot, showToast],
  );
  const handleRequestReject = useCallback(
    async (requestId: number, note?: string) => {
      try {
        await rejectRequest(requestId, note);
        await refreshActivitySnapshot();
        showToast('Request rejected', 'success');
      } catch (error) {
        showToast(getErrorMessage(error, 'Failed to reject request'), 'error');
      }
    },
    [refreshActivitySnapshot, rejectRequest, showToast],
  );
  const handleRequestApprove = useCallback(
    async (
      requestId: number,
      record: RequestRecord,
      options?: { browseOnly?: boolean; manualApproval?: boolean },
    ) => {
      try {
        const bookId = Number(record.book_id);
        if (!Number.isInteger(bookId) || bookId < 1) {
          throw new Error('Request is missing its Book identity');
        }
        if (options?.manualApproval) {
          await fulfilBookRequests(bookId);
          await refreshActivitySnapshot();
          await fetchStatus();
          showToast('Book marked available', 'success');
          return;
        }
        setFulfillingRequest({
          requestId,
          book: bookFromRequestRecord(record),
          contentType: record.content_type,
        });
      } catch (error) {
        showToast(getErrorMessage(error, 'Failed to approve request'), 'error');
      }
    },
    [fetchStatus, fulfilBookRequests, refreshActivitySnapshot, showToast],
  );
  const handleBrowseFulfilDownload = useCallback(
    async (book: Book, release: Release, releaseContentType: ContentType) => {
      if (!fulfillingRequest) return;
      try {
        const bookId = fulfillingRequest.book.book_id;
        if (!bookId) {
          throw new Error('Request is missing its Book identity');
        }
        await fulfilBookRequests(
          bookId,
          buildReleaseDataFromMetadataRelease(book, release, releaseContentType),
        );
        setFulfillingRequest(null);
        await refreshActivitySnapshot();
        await fetchStatus();
        showToast(`Request approved: ${book.title || 'Untitled'}`, 'success');
      } catch (error) {
        showToast(getErrorMessage(error, 'Failed to fulfil request'), 'error');
        throw error;
      }
    },
    [fetchStatus, fulfilBookRequests, fulfillingRequest, refreshActivitySnapshot, showToast],
  );

  const activeReleaseBook = fulfillingRequest?.book ?? releaseBook;
  const activeReleaseContentType = fulfillingRequest?.contentType ?? contentType;
  const bookLanguages = config?.book_languages ?? DEFAULT_LANGUAGES;
  const defaultLanguages = config?.default_language?.length
    ? config.default_language
    : [bookLanguages[0]?.code ?? 'en'];
  const usePinnedMainScrollContainer =
    downloadsSidebarOpen && isDesktopViewport && sidebarPinnedOpen;

  if (!authChecked || (isAuthenticated && !config)) {
    return (
      <div className="sr-only" aria-live="polite">
        Loading...
      </div>
    );
  }

  const app = (
    <>
      <div ref={headerRef} className="fixed top-0 right-0 left-0 z-40">
        <Header
          calibreWebUrl={config?.calibre_web_url}
          audiobookLibraryUrl={config?.audiobook_library_url}
          debug={config?.debug}
          onDownloadsClick={() => {
            setDownloadsSidebarOpen((open) => !open);
            prefetchActivityHistory();
          }}
          onSettingsClick={handleSettingsClick}
          onPersonalSettingsClick={handlePersonalSettingsClick}
          isAdmin={isAdmin}
          canAccessSettings={isAuthenticated}
          statusCounts={statusCounts}
          authRequired={authRequired}
          isAuthenticated={isAuthenticated}
          username={username}
          displayName={displayName}
          onLogout={() => {
            void handleLogout().then(resetActivity);
          }}
          onShowToast={showToast}
          onRemoveToast={removeToast}
        />
      </div>
      <button
        type="button"
        className="fixed top-4 left-4 z-45 rounded-full bg-(--bg) p-2 shadow-sm lg:hidden"
        onClick={() => setLibraryNavigationOpen(true)}
        aria-label="Open navigation"
      >
        <svg
          className="h-5 w-5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>
      <LibraryNavigation
        isOpen={libraryNavigationOpen}
        onClose={() => setLibraryNavigationOpen(false)}
        onSettings={() => {
          setLibraryNavigationOpen(false);
          handleSettingsClick();
        }}
      />
      <div
        className={`library-app-shell flex flex-col${usePinnedMainScrollContainer ? ' min-h-0 overflow-y-auto overscroll-y-contain' : ' flex-1'}`}
        style={
          usePinnedMainScrollContainer
            ? {
                position: 'fixed',
                top: `${headerHeight}px`,
                bottom: 0,
                left: 0,
                right: '25rem',
                zIndex: 20,
              }
            : { paddingTop: `${headerHeight}px` }
        }
      >
        <main className="relative mx-auto w-full max-w-7xl px-4 py-3 sm:px-6 sm:py-6 lg:px-8">
          <Routes>
            <Route path="/" element={<Navigate to="/library" replace />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route
              path="/library/:bookId"
              element={
                <BookDetailPage
                  autoFindReleases={config?.library_auto_find_releases !== false}
                  canFindReleases={isAdmin || libraryCapability === 'download-capable'}
                  isRequestOnly={isRequestOnlyLibraryUser(isAdmin, libraryCapability)}
                  onFindReleases={setReleaseBook}
                  onOpenSettings={handleSettingsClick}
                  onShowToast={showToast}
                />
              }
            />
            <Route path="*" element={<Navigate to="/library" replace />} />
          </Routes>
          {activeReleaseBook && (
            <ReleaseModal
              book={activeReleaseBook}
              onClose={() => {
                setReleaseBook(null);
                setFulfillingRequest(null);
              }}
              onDownload={fulfillingRequest ? handleBrowseFulfilDownload : handleReleaseDownload}
              supportedFormats={config?.supported_formats ?? DEFAULT_SUPPORTED_FORMATS}
              supportedAudiobookFormats={config?.supported_audiobook_formats}
              contentType={activeReleaseContentType}
              defaultLanguages={defaultLanguages}
              bookLanguages={bookLanguages}
              currentStatus={currentStatus}
              defaultReleaseSource={config?.default_release_source}
              defaultAudiobookReleaseSource={config?.default_release_source_audiobook}
              defaultShowManualQuery={activeReleaseBook.provider === 'manual'}
              allowManualQuery={canUseManualReleaseQuery(isAdmin)}
              isRequestMode={Boolean(fulfillingRequest) || activeReleaseBook.provider === 'manual'}
              showReleaseSourceLinks={config?.show_release_source_links !== false}
              onShowToast={showToast}
            />
          )}
        </main>
        <Footer
          buildVersion={config?.build_version}
          releaseVersion={config?.release_version}
          debug={config?.debug}
        />
      </div>
      <ActivitySidebar
        isOpen={downloadsSidebarOpen}
        onClose={() => setDownloadsSidebarOpen(false)}
        status={activitySidebarStatus}
        isAdmin={isAdmin}
        libraryCapability={libraryCapability}
        onClearCompleted={handleClearCompleted}
        onCancel={(id) => void cancelDownload(id).then(fetchStatus)}
        onRetry={(id) => void retryDownload(id).then(fetchStatus)}
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
        showRequestsTab={true}
        isRequestsLoading={isActivitySnapshotLoading}
        onRequestCancel={handleRequestCancel}
        onRequestApprove={isAdmin ? handleRequestApprove : undefined}
        onRequestReject={isAdmin ? handleRequestReject : undefined}
        onRequestDismiss={handleRequestDismiss}
        onPinnedOpenChange={setSidebarPinnedOpen}
        pinnedTopOffset={headerHeight}
      />
      <SettingsModal
        isOpen={settingsOpen}
        authMode={authMode}
        onClose={() => setSettingsOpen(false)}
        onShowToast={showToast}
        onSettingsSaved={() => void loadConfig()}
        onRefreshAuth={refreshAuth}
      />
      <SelfSettingsModal
        isOpen={selfSettingsOpen}
        onClose={() => setSelfSettingsOpen(false)}
        onShowToast={showToast}
        onSettingsSaved={() => void loadConfig()}
      />
      {config && <ConfigSetupBanner settingsEnabled={config.settings_enabled} />}
      <ConfigSetupBanner
        isOpen={configBannerOpen}
        onClose={() => setConfigBannerOpen(false)}
        onContinue={() => {
          setConfigBannerOpen(false);
          handleSettingsClick();
        }}
      />
      <OnboardingModal
        isOpen={onboardingOpen}
        onClose={() => setOnboardingOpen(false)}
        onComplete={() => void loadConfig()}
        onShowToast={showToast}
      />
      <ToastContainer toasts={toasts} />
    </>
  );

  const postLoginPath = getReturnToFromSearch(location.search);
  return (
    <Routes>
      <Route
        path="/login"
        element={
          !authRequired || isAuthenticated ? (
            <Navigate to={postLoginPath} replace />
          ) : (
            <LoginPage
              onLogin={(credentials) => {
                void handleLogin(credentials);
              }}
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
      <Route
        path="/*"
        element={
          authRequired && !isAuthenticated ? (
            <Navigate to={buildLoginRedirectPath(location)} replace />
          ) : (
            app
          )
        }
      />
    </Routes>
  );
}

export { App };
