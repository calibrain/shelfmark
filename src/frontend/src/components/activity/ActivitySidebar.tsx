import { useEffect, useMemo, useRef, useState, type WheelEvent } from 'react';
import { RequestRecord, StatusData } from '../../types';
import { downloadToActivityItem, DownloadStatusKey } from './activityMappers';
import { ActivityItem } from './activityTypes';
import { ActivityCard } from './ActivityCard';
import { Dropdown } from '../Dropdown';

interface ActivitySidebarProps {
  isOpen: boolean;
  onClose: () => void;
  status: StatusData;
  isAdmin: boolean;
  onClearCompleted: (items: ActivityDismissTarget[]) => void;
  onCancel: (id: string) => void;
  onDownloadDismiss?: (bookId: string, linkedRequestId?: number) => void;
  requestItems: ActivityItem[];
  dismissedItemKeys?: string[];
  historyItems?: ActivityItem[];
  historyHasMore?: boolean;
  historyLoading?: boolean;
  onHistoryLoadMore?: () => void;
  onClearHistory?: () => void;
  onActiveTabChange?: (tab: ActivityTabKey) => void;
  pendingRequestCount: number;
  showRequestsTab: boolean;
  isRequestsLoading?: boolean;
  onRequestCancel?: (requestId: number) => Promise<void> | void;
  onRequestApprove?: (
    requestId: number,
    record: RequestRecord,
    options?: {
      browseOnly?: boolean;
      manualApproval?: boolean;
    }
  ) => Promise<void> | void;
  onRequestReject?: (requestId: number, adminNote?: string) => Promise<void> | void;
  onRequestDismiss?: (requestId: number) => void;
  onPinnedOpenChange?: (pinnedOpen: boolean) => void;
  pinnedTopOffset?: number;
}

export interface ActivityDismissTarget {
  itemType: 'download' | 'request';
  itemKey: string;
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

type ActivityCategoryKey =
  | 'needs_review'
  | 'in_progress'
  | 'complete'
  | 'failed';

type ActivityTabKey = 'all' | 'downloads' | 'requests' | 'history';
const ALL_USERS_FILTER = '__all_users__';

const getCategoryLabel = (
  key: ActivityCategoryKey,
  isAdmin: boolean
): string => {
  if (key === 'needs_review') {
    return isAdmin ? 'Needs Review' : 'Waiting';
  }
  if (key === 'in_progress') {
    return 'In Progress';
  }
  if (key === 'complete') {
    return 'Complete';
  }
  return 'Failed';
};

const getVisibleCategoryOrder = (
  tab: ActivityTabKey
): ActivityCategoryKey[] => {
  if (tab === 'downloads') {
    return ['in_progress', 'complete', 'failed'];
  }
  if (tab === 'requests') {
    return ['needs_review', 'in_progress', 'complete', 'failed'];
  }
  if (tab === 'history') {
    return [];
  }
  return ['needs_review', 'in_progress', 'complete', 'failed'];
};

const getActivityCategory = (item: ActivityItem): ActivityCategoryKey => {
  if (item.kind === 'download') {
    if (
      item.visualStatus === 'queued' ||
      item.visualStatus === 'resolving' ||
      item.visualStatus === 'locating' ||
      item.visualStatus === 'downloading'
    ) {
      return 'in_progress';
    }
    if (item.visualStatus === 'complete') {
      return 'complete';
    }
    return 'failed';
  }

  const requestStatus = item.requestRecord?.status;
  if (requestStatus === 'pending' || item.visualStatus === 'pending') {
    return 'needs_review';
  }

  if (requestStatus === 'rejected' || requestStatus === 'cancelled') {
    return 'failed';
  }

  const deliveryState = item.requestRecord?.delivery_state;
  if (requestStatus === 'fulfilled' || item.visualStatus === 'fulfilled') {
    if (
      deliveryState === 'queued' ||
      deliveryState === 'resolving' ||
      deliveryState === 'locating' ||
      deliveryState === 'downloading'
    ) {
      return 'in_progress';
    }
    if (deliveryState === 'error' || deliveryState === 'cancelled') {
      return 'failed';
    }
    // Legacy fulfilled requests often have unknown/none delivery state because the
    // pre-refactor queue state was ephemeral. Treat as completed approval, not in-progress.
    return 'complete';
  }

  if (deliveryState === 'complete') {
    return 'complete';
  }
  if (deliveryState === 'error' || deliveryState === 'cancelled') {
    return 'failed';
  }
  return 'in_progress';
};

const getLinkedDownloadIdFromRequestItem = (item: ActivityItem): string | null => {
  if (item.kind !== 'request' || item.visualStatus !== 'fulfilled') {
    return null;
  }

  const releaseData = item.requestRecord?.release_data;
  if (!releaseData || typeof releaseData !== 'object') {
    return null;
  }

  const sourceId = (releaseData as Record<string, unknown>).source_id;
  if (typeof sourceId !== 'string') {
    return null;
  }

  const trimmed = sourceId.trim();
  return trimmed ? trimmed : null;
};

const mergeRequestWithDownload = (
  requestItem: ActivityItem,
  downloadItem: ActivityItem
): ActivityItem => {
  return {
    ...downloadItem,
    id: requestItem.id,
    kind: 'download',
    title: downloadItem.title || requestItem.title,
    author: downloadItem.author || requestItem.author,
    preview: downloadItem.preview || requestItem.preview,
    metaLine: downloadItem.metaLine,
    timestamp: Math.max(downloadItem.timestamp, requestItem.timestamp),
    username: requestItem.username || downloadItem.username,
    adminNote: requestItem.adminNote,
    requestId: requestItem.requestId,
    requestLevel: requestItem.requestLevel,
    requestNote: requestItem.requestNote,
    requestRecord: requestItem.requestRecord,
  };
};

const dedupeById = (items: ActivityItem[]): ActivityItem[] => {
  const byId = new Map<string, ActivityItem>();
  items.forEach((item) => {
    const current = byId.get(item.id);
    if (!current || item.timestamp >= current.timestamp) {
      byId.set(item.id, item);
    }
  });
  return Array.from(byId.values());
};

const getItemUsername = (item: ActivityItem): string | null => {
  const candidate = item.username || item.requestRecord?.username;
  if (typeof candidate !== 'string') {
    return null;
  }
  const normalized = candidate.trim();
  return normalized || null;
};

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
  onDownloadDismiss,
  requestItems,
  dismissedItemKeys = [],
  historyItems = [],
  historyHasMore = false,
  historyLoading = false,
  onHistoryLoadMore,
  onClearHistory,
  onActiveTabChange,
  pendingRequestCount,
  showRequestsTab,
  isRequestsLoading = false,
  onRequestCancel,
  onRequestApprove,
  onRequestReject,
  onRequestDismiss,
  onPinnedOpenChange,
  pinnedTopOffset = 0,
}: ActivitySidebarProps) => {
  const [isPinned, setIsPinned] = useState<boolean>(() => getInitialPinnedPreference());
  const [isDesktop, setIsDesktop] = useState<boolean>(() => getInitialDesktopState());
  const [activeTab, setActiveTab] = useState<ActivityTabKey>('all');
  const [selectedUser, setSelectedUser] = useState<string>(ALL_USERS_FILTER);
  const [rejectingRequest, setRejectingRequest] = useState<{ requestId: number } | null>(null);
  const [reviewingRequestId, setReviewingRequestId] = useState<number | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const scrollViewportRef = useRef<HTMLDivElement | null>(null);
  const dismissedKeySet = useMemo(
    () => new Set(dismissedItemKeys),
    [dismissedItemKeys]
  );

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
    onActiveTabChange?.(activeTab);
  }, [activeTab, onActiveTabChange]);

  useEffect(() => {
    if (activeTab === 'downloads') {
      setRejectingRequest(null);
      setReviewingRequestId(null);
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
        const itemKey = `download:${book.id}`;
        const isTerminalStatus =
          statusKey === 'complete' || statusKey === 'error' || statusKey === 'cancelled';
        if (isTerminalStatus && dismissedKeySet.has(itemKey)) {
          return;
        }
        items.push(downloadToActivityItem(book, statusKey));
      });
    });

    return items.sort((left, right) => right.timestamp - left.timestamp);
  }, [dismissedKeySet, status]);

  const visibleRequestItems = useMemo(
    () =>
      requestItems.filter((item) => {
        if (!item.requestId) {
          return true;
        }
        return !dismissedKeySet.has(`request:${item.requestId}`);
      }),
    [dismissedKeySet, requestItems]
  );

  const { mergedRequestItems, mergedDownloadItems } = useMemo(() => {
    const downloadsById = new Map<string, ActivityItem>();
    downloadItems.forEach((item) => {
      if (item.downloadBookId) {
        downloadsById.set(item.downloadBookId, item);
      }
    });

    const mergedByDownloadId = new Map<string, ActivityItem>();
    const reopenedRequestIds = new Set<number>();

    visibleRequestItems.forEach((item) => {
      if (item.kind !== 'request' || typeof item.requestId !== 'number') {
        return;
      }
      const requestRecord = item.requestRecord;
      const failureReason = requestRecord?.last_failure_reason;
      if (
        requestRecord?.status === 'pending' &&
        typeof failureReason === 'string' &&
        failureReason.trim().length > 0
      ) {
        reopenedRequestIds.add(item.requestId);
      }
    });

    const nextRequestItems = visibleRequestItems.map((requestItem) => {
      const linkedDownloadId = getLinkedDownloadIdFromRequestItem(requestItem);
      if (!linkedDownloadId) {
        return requestItem;
      }

      const matchedDownload = downloadsById.get(linkedDownloadId);
      if (!matchedDownload) {
        return requestItem;
      }

      const merged = mergeRequestWithDownload(requestItem, matchedDownload);
      if (!mergedByDownloadId.has(linkedDownloadId)) {
        mergedByDownloadId.set(linkedDownloadId, merged);
      }
      return merged;
    });

    const nextDownloadItems = downloadItems.map((downloadItem) => {
      const downloadId = downloadItem.downloadBookId;
      if (!downloadId) {
        return downloadItem;
      }
      return mergedByDownloadId.get(downloadId) || downloadItem;
    }).filter((downloadItem) => {
      if (
        typeof downloadItem.requestId === 'number' &&
        reopenedRequestIds.has(downloadItem.requestId) &&
        (downloadItem.visualStatus === 'error' || downloadItem.visualStatus === 'cancelled')
      ) {
        return false;
      }
      return true;
    });

    return {
      mergedRequestItems: nextRequestItems,
      mergedDownloadItems: nextDownloadItems,
    };
  }, [downloadItems, visibleRequestItems]);

  const hasTerminalDownloadItems = useMemo(
    () =>
      mergedDownloadItems.some(
        (item) =>
          item.visualStatus === 'complete' || item.visualStatus === 'error' || item.visualStatus === 'cancelled'
      ),
    [mergedDownloadItems]
  );

  const allItems = useMemo(() => {
    const combined = dedupeById([...mergedDownloadItems, ...mergedRequestItems]);
    return combined.sort((a, b) => b.timestamp - a.timestamp);
  }, [mergedDownloadItems, mergedRequestItems]);

  const baseVisibleItems = activeTab === 'all'
    ? allItems
    : activeTab === 'requests'
      ? mergedRequestItems.filter((item) => {
          const requestStatus = item.requestRecord?.status;
          if (requestStatus === 'pending' || requestStatus === 'rejected' || requestStatus === 'cancelled') {
            return true;
          }
          return requestStatus === 'fulfilled' && item.kind === 'request';
        })
      : activeTab === 'history'
        ? historyItems
        : mergedDownloadItems;

  const availableUsers = useMemo(() => {
    const userMap = new Map<string, string>();
    baseVisibleItems.forEach((item) => {
      const username = getItemUsername(item);
      if (!username) {
        return;
      }
      const lookupKey = username.toLowerCase();
      if (!userMap.has(lookupKey)) {
        userMap.set(lookupKey, username);
      }
    });

    return Array.from(userMap.values()).sort((left, right) => left.localeCompare(right));
  }, [baseVisibleItems]);

  useEffect(() => {
    if (selectedUser === ALL_USERS_FILTER) {
      return;
    }
    if (!availableUsers.includes(selectedUser)) {
      setSelectedUser(ALL_USERS_FILTER);
    }
  }, [availableUsers, selectedUser]);

  const visibleItems = useMemo(() => {
    if (selectedUser === ALL_USERS_FILTER) {
      return baseVisibleItems;
    }
    return baseVisibleItems.filter((item) => getItemUsername(item) === selectedUser);
  }, [baseVisibleItems, selectedUser]);

  useEffect(() => {
    if (reviewingRequestId === null) {
      return;
    }

    const hasMatchingPendingRequest = visibleItems.some((item) => {
      return (
        item.kind === 'request' &&
        item.requestId === reviewingRequestId &&
        item.requestRecord?.status === 'pending'
      );
    });

    if (!hasMatchingPendingRequest) {
      setReviewingRequestId(null);
    }
  }, [reviewingRequestId, visibleItems]);

  useEffect(() => {
    if (rejectingRequest === null) {
      return;
    }

    const hasMatchingPendingRequest = visibleItems.some((item) => {
      return (
        item.kind === 'request' &&
        item.requestId === rejectingRequest.requestId &&
        item.requestRecord?.status === 'pending'
      );
    });

    if (!hasMatchingPendingRequest) {
      setRejectingRequest(null);
    }
  }, [rejectingRequest, visibleItems]);

  const hasUserFilter = isAdmin && availableUsers.length > 1;

  const clearCompletedTargets = useMemo(() => {
    const targets: ActivityDismissTarget[] = [];
    const seen = new Set<string>();

    visibleItems.forEach((item) => {
      const isTerminalDownload =
        item.kind === 'download' &&
        (item.visualStatus === 'complete' || item.visualStatus === 'error' || item.visualStatus === 'cancelled');

      if (!isTerminalDownload || !item.downloadBookId) {
        return;
      }

      const downloadKey = `download:${item.downloadBookId}`;
      if (!seen.has(downloadKey)) {
        seen.add(downloadKey);
        targets.push({ itemType: 'download', itemKey: downloadKey });
      }

      if (item.requestId) {
        const requestKey = `request:${item.requestId}`;
        if (!seen.has(requestKey)) {
          seen.add(requestKey);
          targets.push({ itemType: 'request', itemKey: requestKey });
        }
      }
    });

    return targets;
  }, [visibleItems]);

  const visibleCategoryOrder = useMemo(
    () => getVisibleCategoryOrder(activeTab),
    [activeTab]
  );

  const groupedVisibleItems = useMemo(() => {
    if (activeTab === 'history') {
      return [];
    }

    const grouped = new Map<ActivityCategoryKey, ActivityItem[]>();
    visibleCategoryOrder.forEach((key) => grouped.set(key, []));

    visibleItems.forEach((item) => {
      const category = getActivityCategory(item);
      if (!grouped.has(category)) {
        grouped.set(category, []);
      }
      grouped.get(category)!.push(item);
    });

    return visibleCategoryOrder
      .map((key) => ({
        key,
        label: getCategoryLabel(key, isAdmin),
        items: (grouped.get(key) || []).sort((left, right) => right.timestamp - left.timestamp),
      }))
      .filter((group) => group.items.length > 0);
  }, [activeTab, isAdmin, visibleItems, visibleCategoryOrder]);

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
    if (!activeButton) {
      setTabIndicatorStyle({ left: 0, width: 0 });
      return;
    }

    const containerRect = activeButton.parentElement?.getBoundingClientRect();
    const buttonRect = activeButton.getBoundingClientRect();
    if (containerRect) {
      setTabIndicatorStyle({
        left: buttonRect.left - containerRect.left,
        width: buttonRect.width,
      });
    }
  }, [activeTab, showRequestsTab]);

  const panel = (
    <>
      <div
        className="px-4 pt-4 pb-0"
        style={{
          borderColor: 'var(--border-muted)',
          paddingTop: 'calc(1rem + env(safe-area-inset-top))',
        }}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">{activeTab === 'history' ? 'History' : 'Activity'}</h2>
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
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m9 15-6 6M15 6l-1-1 2-2 5 5-2 2-1-1-4.5 4.5c1.5 1.5 1 4-.5 5.5l-8-8c1.5-1.5 4-2 5.5-.5z" />
                </svg>
              )}
            </button>
          </div>

          <div className="flex items-center gap-1">
            {hasUserFilter && (
              <Dropdown
                align="right"
                widthClassName="w-auto"
                panelClassName="min-w-[11rem]"
                renderTrigger={({ isOpen, toggle }) => (
                  <button
                    type="button"
                    onClick={toggle}
                    className={`h-9 w-9 inline-flex items-center justify-center rounded-full hover-action transition-colors ${
                      isOpen || selectedUser !== ALL_USERS_FILTER ? 'text-sky-600 dark:text-sky-400' : ''
                    }`}
                    title={selectedUser === ALL_USERS_FILTER ? 'Filter by user' : `Filtered: ${selectedUser}`}
                    aria-label={selectedUser === ALL_USERS_FILTER ? 'Filter by user' : `Filtered by user ${selectedUser}`}
                    aria-expanded={isOpen}
                  >
                    <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" strokeWidth="1.75" stroke="currentColor" aria-hidden="true">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z" />
                    </svg>
                  </button>
                )}
              >
                {({ close }) => (
                  <div role="listbox">
                    {[ALL_USERS_FILTER, ...availableUsers].map((value) => {
                      const isSelected = selectedUser === value;
                      const label = value === ALL_USERS_FILTER ? 'All users' : value;
                      return (
                        <button
                          type="button"
                          key={value}
                          className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${
                            isSelected ? 'text-sky-600 dark:text-sky-400' : ''
                          }`}
                          onClick={() => {
                            setSelectedUser(value);
                            close();
                          }}
                        >
                          <span>{label}</span>
                          {isSelected && (
                            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />
                            </svg>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </Dropdown>
            )}
            <button
              type="button"
              onClick={() => setActiveTab((current) => (current === 'history' ? 'all' : 'history'))}
              className={`relative h-9 w-9 inline-flex items-center justify-center rounded-full hover-action transition-colors ${
                activeTab === 'history' ? 'text-sky-600 dark:text-sky-400' : ''
              }`}
              title={activeTab === 'history' ? 'Back to activity' : 'Open history'}
              aria-label={activeTab === 'history' ? 'Back to activity' : 'Open history'}
              aria-pressed={activeTab === 'history'}
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6l3.75 2.25" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 3v4.5h4.5" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12a8.25 8.25 0 1 0 3.37-6.63" />
              </svg>
            </button>
            <button
              type="button"
              onClick={onClose}
              className="h-9 w-9 inline-flex items-center justify-center rounded-full hover-action transition-colors"
              aria-label="Close activity sidebar"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {activeTab !== 'history' && (
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
                {mergedDownloadItems.length > 0 && (
                  <span className="ml-1.5 text-[11px] h-[18px] min-w-[18px] px-1 rounded-full bg-sky-500/15 text-sky-700 dark:text-sky-300 inline-flex items-center justify-center leading-none">
                    {mergedDownloadItems.length}
                  </span>
                )}
              </button>
              {showRequestsTab && (
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
                    <span className="ml-1.5 text-[11px] h-[18px] min-w-[18px] px-1 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 inline-flex items-center justify-center leading-none">
                      {pendingRequestCount}
                    </span>
                  )}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <div
        ref={scrollViewportRef}
        className="flex-1 overflow-y-auto overscroll-y-contain p-4"
        style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))' }}
      >
        {visibleItems.length === 0 ? (
          <p className="text-center text-sm opacity-70 mt-8">
            {activeTab === 'requests'
              ? isRequestsLoading ? 'Loading requests...' : 'No requests'
              : activeTab === 'history'
                ? historyLoading ? 'Loading history...' : 'No history'
              : activeTab === 'downloads'
                ? 'No downloads'
                : 'No activity'}
          </p>
        ) : (
          activeTab === 'history' ? (
            <div className="divide-y divide-[color-mix(in_srgb,var(--border-muted)_60%,transparent)]">
              {visibleItems.map((item) => (
                <ActivityCard
                  key={item.id}
                  item={item}
                  isAdmin={isAdmin}
                />
              ))}
              {historyHasMore && (
                <div className="pt-3 text-center">
                  <button
                    type="button"
                    onClick={onHistoryLoadMore}
                    disabled={historyLoading}
                    className="text-sm text-sky-600 dark:text-sky-400 hover:underline disabled:opacity-60"
                  >
                    {historyLoading ? 'Loading...' : 'Load more'}
                  </button>
                </div>
              )}
            </div>
          ) : (
          groupedVisibleItems.map((group) => (
            <section key={group.key} className="mb-4 last:mb-0">
              {activeTab !== 'downloads' && (
                <button
                  type="button"
                  onClick={() => setCollapsedGroups((prev) => ({ ...prev, [group.key]: !prev[group.key] }))}
                  className="mb-2 w-full flex items-center justify-between text-[11px] uppercase tracking-wide opacity-70 hover:opacity-100 transition-opacity cursor-pointer"
                >
                  <div className="flex items-center gap-1.5">
                    <svg
                      className={`w-3 h-3 transition-transform ${collapsedGroups[group.key] ? '-rotate-90' : ''}`}
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                      strokeWidth="1.5"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                    </svg>
                    <span>{group.label}</span>
                  </div>
                  <span className="rounded-full h-[18px] min-w-[18px] px-1 bg-gray-500/10 dark:bg-gray-400/10 inline-flex items-center justify-center leading-none">{group.items.length}</span>
                </button>
              )}
              {!collapsedGroups[group.key] && (
              <div className="divide-y divide-[color-mix(in_srgb,var(--border-muted)_60%,transparent)]">
                {group.items.map((item) => {
                  const showRequestActions = activeTab === 'requests' || activeTab === 'all';
                  const requestId = item.requestId;
                  const shouldShowRejectDialog =
                    showRequestActions &&
                    rejectingRequest !== null &&
                    requestId === rejectingRequest.requestId;
                  const requestRecord = item.requestRecord;
                  const canShowRequestReview =
                    showRequestActions &&
                    isAdmin &&
                    item.kind === 'request' &&
                    typeof requestId === 'number' &&
                    requestRecord?.status === 'pending';
                  const shouldShowRequestReview =
                    canShowRequestReview &&
                    reviewingRequestId !== null &&
                    requestId === reviewingRequestId &&
                    requestRecord !== undefined;

                  return (
                    <div key={item.id}>
                      <ActivityCard
                        item={item}
                        isAdmin={isAdmin}
                        onDownloadCancel={onCancel}
                        onDownloadDismiss={onDownloadDismiss}
                        onRequestCancel={onRequestCancel}
                        onRequestApprove={onRequestApprove}
                        onRequestDismiss={onRequestDismiss}
                        onRequestReject={
                          showRequestActions && onRequestReject
                            ? (requestId) => {
                                setReviewingRequestId(null);
                                setRejectingRequest({ requestId });
                              }
                            : undefined
                        }
                        showRequestDetailsToggle={canShowRequestReview}
                        isRequestDetailsOpen={shouldShowRequestReview}
                        isSelected={shouldShowRequestReview || shouldShowRejectDialog}
                        onRequestReviewApprove={
                          onRequestApprove
                            ? async (requestId, record, options) => {
                                await onRequestApprove(requestId, record, options);
                                setReviewingRequestId(null);
                              }
                            : undefined
                        }
                        isRequestRejectOpen={shouldShowRejectDialog}
                        onRequestRejectClose={() => setRejectingRequest(null)}
                        onRequestRejectConfirm={
                          onRequestReject
                            ? async (requestId, adminNote) => {
                                await onRequestReject(requestId, adminNote);
                                setRejectingRequest(null);
                              }
                            : undefined
                        }
                        onRequestDetailsToggle={
                          canShowRequestReview && typeof requestId === 'number'
                            ? () => {
                                if (shouldShowRejectDialog) {
                                  setRejectingRequest(null);
                                  return;
                                }
                                setRejectingRequest(null);
                                setReviewingRequestId((current) => (
                                  current === requestId ? null : requestId
                                ));
                              }
                            : undefined
                        }
                        onRequestDetailsOpen={
                          canShowRequestReview && typeof requestId === 'number'
                            ? () => {
                                setRejectingRequest(null);
                                setReviewingRequestId(requestId);
                              }
                            : undefined
                        }
                      />
                    </div>
                  );
                })}
              </div>
              )}
            </section>
          ))
          )
        )}
      </div>

      {(activeTab === 'downloads' || activeTab === 'all') && hasTerminalDownloadItems && clearCompletedTargets.length > 0 && (
        <div
          className="p-3 border-t flex items-center justify-center"
          style={{
            borderColor: 'var(--border-muted)',
            paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom))',
          }}
        >
          <button
            type="button"
            onClick={() => onClearCompleted(clearCompletedTargets)}
            className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            Clear Completed
          </button>
        </div>
      )}

      {activeTab === 'history' && historyItems.length > 0 && (
        <div
          className="p-3 border-t flex items-center justify-center"
          style={{
            borderColor: 'var(--border-muted)',
            paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom))',
          }}
        >
          <button
            type="button"
            onClick={onClearHistory}
            className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            Clear History
          </button>
        </div>
      )}
    </>
  );

  if (isPinnedOpen) {
    const handlePinnedWheel = (event: WheelEvent<HTMLElement>) => {
      const viewport = scrollViewportRef.current;
      if (!viewport) {
        return;
      }
      // Keep wheel/trackpad scrolling contained to the pinned activity panel.
      event.preventDefault();
      event.stopPropagation();
      viewport.scrollTop += event.deltaY;
    };

    return (
      <aside
        className="hidden lg:flex fixed right-0 w-96 flex-col bg-[var(--bg-soft)] z-30 rounded-2xl shadow-lg overflow-hidden"
        style={{
          top: `${pinnedTopOffset}px`,
          height: `calc(100dvh - ${pinnedTopOffset}px - 0.75rem)`,
          right: '0.75rem',
        }}
        onWheel={handlePinnedWheel}
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
        style={{ background: 'var(--bg)' }}
        aria-hidden={!isOpen}
      >
        {panel}
      </aside>
    </>
  );
};
