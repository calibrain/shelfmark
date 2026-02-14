import { useEffect, useMemo, useRef, useState } from 'react';
import { RequestRecord, StatusData } from '../../types';
import { downloadToActivityItem, DownloadStatusKey } from './activityMappers';
import { ActivityItem } from './activityTypes';
import { ActivityCard } from './ActivityCard';
import { RejectDialog } from './RejectDialog';

interface ActivitySidebarProps {
  isOpen: boolean;
  onClose: () => void;
  status: StatusData;
  isAdmin: boolean;
  onClearCompleted: () => void;
  onCancel: (id: string) => void;
  requestItems: ActivityItem[];
  pendingRequestCount: number;
  showRequestsTab: boolean;
  isRequestsLoading?: boolean;
  onRequestCancel?: (requestId: number) => Promise<void> | void;
  onRequestApprove?: (requestId: number, record: RequestRecord) => Promise<void> | void;
  onRequestReject?: (requestId: number, adminNote?: string) => Promise<void> | void;
  onPinnedOpenChange?: (pinnedOpen: boolean) => void;
  pinnedTopOffset?: number;
}

export const ACTIVITY_SIDEBAR_PINNED_STORAGE_KEY = 'activity-sidebar-pinned';

const DOWNLOAD_STATUS_KEYS: DownloadStatusKey[] = [
  'downloading',
  'locating',
  'resolving',
  'queued',
  'error',
  'complete',
  'cancelled',
];

const parsePinned = (value: string | null): boolean => {
  if (!value) {
    return false;
  }
  return value === '1' || value.toLowerCase() === 'true';
};

const getInitialPinnedPreference = (): boolean => {
  if (typeof window === 'undefined') {
    return false;
  }
  try {
    return parsePinned(window.localStorage.getItem(ACTIVITY_SIDEBAR_PINNED_STORAGE_KEY));
  } catch {
    return false;
  }
};

const getInitialDesktopState = (): boolean => {
  if (typeof window === 'undefined') {
    return false;
  }
  return window.matchMedia('(min-width: 1024px)').matches;
};

export const ActivitySidebar = ({
  isOpen,
  onClose,
  status,
  isAdmin,
  onClearCompleted,
  onCancel,
  requestItems,
  pendingRequestCount,
  showRequestsTab,
  isRequestsLoading = false,
  onRequestCancel,
  onRequestApprove,
  onRequestReject,
  onPinnedOpenChange,
  pinnedTopOffset = 0,
}: ActivitySidebarProps) => {
  const [isPinned, setIsPinned] = useState<boolean>(() => getInitialPinnedPreference());
  const [isDesktop, setIsDesktop] = useState<boolean>(() => getInitialDesktopState());
  const [activeTab, setActiveTab] = useState<'all' | 'downloads' | 'requests'>('all');
  const [rejectingRequest, setRejectingRequest] = useState<{ requestId: number; bookTitle: string } | null>(null);

  useEffect(() => {
    const mediaQuery = window.matchMedia('(min-width: 1024px)');

    const handleMediaChange = (event: MediaQueryListEvent) => {
      setIsDesktop(event.matches);
    };

    setIsDesktop(mediaQuery.matches);
    mediaQuery.addEventListener('change', handleMediaChange);

    return () => {
      mediaQuery.removeEventListener('change', handleMediaChange);
    };
  }, []);

  useEffect(() => {
    if (!showRequestsTab && activeTab === 'requests') {
      setActiveTab('all');
    }
  }, [showRequestsTab, activeTab]);

  useEffect(() => {
    if (activeTab === 'downloads') {
      setRejectingRequest(null);
    }
  }, [activeTab]);

  const isPinnedOpen = isOpen && isDesktop && isPinned;

  useEffect(() => {
    onPinnedOpenChange?.(isPinnedOpen);
  }, [isPinnedOpen, onPinnedOpenChange]);

  useEffect(() => {
    if (!isOpen || isPinnedOpen) {
      return;
    }

    const onEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', onEscape);
    return () => document.removeEventListener('keydown', onEscape);
  }, [isOpen, isPinnedOpen, onClose]);

  const downloadItems = useMemo(() => {
    const items: ActivityItem[] = [];

    DOWNLOAD_STATUS_KEYS.forEach((statusKey) => {
      const bucket = status[statusKey];
      if (!bucket) {
        return;
      }
      Object.values(bucket).forEach((book) => {
        items.push(downloadToActivityItem(book, statusKey));
      });
    });

    return items.sort((left, right) => right.timestamp - left.timestamp);
  }, [status]);

  const hasTerminalDownloadItems = useMemo(
    () =>
      downloadItems.some(
        (item) =>
          item.visualStatus === 'complete' || item.visualStatus === 'error' || item.visualStatus === 'cancelled'
      ),
    [downloadItems]
  );

  const allItems = useMemo(() => {
    return [...downloadItems, ...requestItems].sort((a, b) => b.timestamp - a.timestamp);
  }, [downloadItems, requestItems]);

  const visibleItems = activeTab === 'all' ? allItems : activeTab === 'requests' ? requestItems : downloadItems;

  const handleTogglePinned = () => {
    const next = !isPinned;
    setIsPinned(next);
    try {
      window.localStorage.setItem(ACTIVITY_SIDEBAR_PINNED_STORAGE_KEY, next ? '1' : '0');
    } catch {
      // Ignore storage failures
    }
  };

  // Tab indicator (sliding underline, same pattern as ReleaseModal)
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [tabIndicatorStyle, setTabIndicatorStyle] = useState({ left: 0, width: 0 });

  useEffect(() => {
    const activeButton = tabRefs.current[activeTab];
    if (activeButton) {
      const containerRect = activeButton.parentElement?.getBoundingClientRect();
      const buttonRect = activeButton.getBoundingClientRect();
      if (containerRect) {
        setTabIndicatorStyle({
          left: buttonRect.left - containerRect.left,
          width: buttonRect.width,
        });
      }
    }
  }, [activeTab, showRequestsTab]);

  const panel = (
    <>
      <div
        className={`px-4 pt-4 ${showRequestsTab ? 'pb-0' : 'pb-4 border-b'}`}
        style={{
          borderColor: 'var(--border-muted)',
          paddingTop: 'calc(1rem + env(safe-area-inset-top))',
        }}
      >
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Activity</h2>

          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={handleTogglePinned}
              className="hidden lg:inline-flex h-9 w-9 items-center justify-center rounded-full hover-action transition-colors"
              title={isPinned ? 'Unpin activity sidebar' : 'Pin activity sidebar'}
              aria-label={isPinned ? 'Unpin activity sidebar' : 'Pin activity sidebar'}
            >
              {isPinned ? (
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M15.804 2.276a.75.75 0 0 0-.336.195l-2 2a.75.75 0 0 0 0 1.062l.47.469-3.572 3.571c-.83-.534-1.773-.808-2.709-.691-1.183.148-2.32.72-3.187 1.587a.75.75 0 0 0 0 1.063L7.938 15l-5.467 5.467a.75.75 0 0 0 0 1.062.75.75 0 0 0 1.062 0L9 16.062l3.468 3.468a.75.75 0 0 0 1.062 0c.868-.868 1.44-2.004 1.588-3.187.117-.935-.158-1.879-.692-2.708L18 10.063l.469.469a.75.75 0 0 0 1.062 0l2-2a.75.75 0 0 0 0-1.062l-5-4.999a.75.75 0 0 0-.726-.195z" />
                </svg>
              ) : (
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m9 15-6 6M15 6l-1-1 2-2 5 5-2 2-1-1-4.5 4.5c1.5 1.5 1 4-.5 5.5l-8-8c1.5-1.5 4-2 5.5-.5z" />
                </svg>
              )}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="h-9 w-9 inline-flex items-center justify-center rounded-full hover-action transition-colors"
              aria-label="Close activity sidebar"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {showRequestsTab && (
          <div className="mt-2 border-b border-[var(--border-muted)] -mx-4 px-4">
            <div className="relative flex gap-1">
              {/* Sliding indicator */}
              <div
                className="absolute bottom-0 h-0.5 bg-sky-500 transition-all duration-300 ease-out"
                style={{
                  left: tabIndicatorStyle.left,
                  width: tabIndicatorStyle.width,
                }}
              />
              <button
                type="button"
                ref={(el) => { tabRefs.current.all = el; }}
                onClick={() => setActiveTab('all')}
                className={`px-4 py-2.5 text-sm font-medium border-b-2 border-transparent transition-colors whitespace-nowrap ${
                  activeTab === 'all'
                    ? 'text-sky-600 dark:text-sky-400'
                    : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                }`}
                aria-current={activeTab === 'all' ? 'page' : undefined}
              >
                All
              </button>
              <button
                type="button"
                ref={(el) => { tabRefs.current.downloads = el; }}
                onClick={() => setActiveTab('downloads')}
                className={`px-4 py-2.5 text-sm font-medium border-b-2 border-transparent transition-colors whitespace-nowrap ${
                  activeTab === 'downloads'
                    ? 'text-sky-600 dark:text-sky-400'
                    : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                }`}
                aria-current={activeTab === 'downloads' ? 'page' : undefined}
              >
                Downloads
                {downloadItems.length > 0 && (
                  <span className="ml-1.5 text-[11px] px-1.5 py-0.5 rounded-full bg-sky-500/15 text-sky-700 dark:text-sky-300">
                    {downloadItems.length}
                  </span>
                )}
              </button>
              <button
                type="button"
                ref={(el) => { tabRefs.current.requests = el; }}
                onClick={() => setActiveTab('requests')}
                className={`px-4 py-2.5 text-sm font-medium border-b-2 border-transparent transition-colors whitespace-nowrap ${
                  activeTab === 'requests'
                    ? 'text-sky-600 dark:text-sky-400'
                    : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                }`}
                aria-current={activeTab === 'requests' ? 'page' : undefined}
              >
                Requests
                {pendingRequestCount > 0 && (
                  <span className="ml-1.5 text-[11px] px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300">
                    {pendingRequestCount}
                  </span>
                )}
              </button>
            </div>
          </div>
        )}
      </div>

      <div
        className="flex-1 overflow-y-auto p-4 divide-y divide-[var(--border-muted)]"
        style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))' }}
      >
        {visibleItems.length === 0 ? (
          <p className="text-center text-sm opacity-70 mt-8">
            {activeTab === 'requests'
              ? isRequestsLoading ? 'Loading requests...' : 'No requests'
              : activeTab === 'downloads'
                ? 'No downloads'
                : 'No activity'}
          </p>
        ) : (
          visibleItems.map((item) => {
            const showRequestActions = activeTab === 'requests' || activeTab === 'all';
            const shouldShowRejectDialog =
              showRequestActions &&
              rejectingRequest !== null &&
              item.requestId === rejectingRequest.requestId;

            return (
              <div key={item.id} className="py-2 first:pt-0 last:pb-0">
                <ActivityCard
                  item={item}
                  isAdmin={isAdmin}
                  onDownloadCancel={onCancel}
                  onRequestCancel={onRequestCancel}
                  onRequestApprove={onRequestApprove}
                  onRequestReject={
                    showRequestActions && onRequestReject
                      ? (requestId) => {
                          const title = item.title || 'Untitled request';
                          setRejectingRequest({ requestId, bookTitle: title });
                        }
                      : undefined
                  }
                />
                {shouldShowRejectDialog && onRequestReject && (
                  <RejectDialog
                    requestId={rejectingRequest.requestId}
                    bookTitle={rejectingRequest.bookTitle}
                    onConfirm={onRequestReject}
                    onCancel={() => setRejectingRequest(null)}
                  />
                )}
              </div>
            );
          })
        )}
      </div>

      {(activeTab === 'downloads' || activeTab === 'all') && hasTerminalDownloadItems && (
        <div
          className="p-3 border-t flex items-center justify-center"
          style={{
            borderColor: 'var(--border-muted)',
            paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom))',
          }}
        >
          <button
            type="button"
            onClick={onClearCompleted}
            className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            Clear Completed
          </button>
        </div>
      )}
    </>
  );

  if (isPinnedOpen) {
    return (
      <aside
        className="hidden lg:flex fixed right-0 w-96 flex-col bg-[var(--bg-soft)] z-30 rounded-2xl shadow-lg overflow-hidden"
        style={{
          top: `calc(${pinnedTopOffset}px + 0.75rem)`,
          height: `calc(100vh - ${pinnedTopOffset}px - 1.5rem)`,
          right: '0.75rem',
        }}
        aria-hidden={!isOpen}
      >
        {panel}
      </aside>
    );
  }

  return (
    <>
      <div
        className={`fixed inset-0 bg-black/50 z-[45] transition-opacity duration-300 ${
          isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={onClose}
      />

      <aside
        className={`fixed top-0 right-0 h-full w-full sm:w-96 z-50 flex flex-col shadow-2xl transition-transform duration-300 ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ background: 'var(--bg-soft)' }}
        aria-hidden={!isOpen}
      >
        {panel}
      </aside>
    </>
  );
};
