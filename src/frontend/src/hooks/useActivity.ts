import { useCallback, useEffect, useMemo, useState } from 'react';
import { Socket } from 'socket.io-client';
import { Book, RequestRecord, StatusData } from '../types';
import {
  ActivityHistoryItem,
  ActivityDismissPayload,
  clearActivityHistory,
  dismissActivityItem,
  dismissManyActivityItems,
  getActivitySnapshot,
  listActivityHistory,
} from '../services/api';
import {
  ActivityDismissTarget,
  ActivityItem,
  downloadToActivityItem,
  requestToActivityItem,
} from '../components/activity';

const HISTORY_PAGE_SIZE = 50;

const parseTimestamp = (value: string | null | undefined, fallback: number = 0): number => {
  if (!value) {
    return fallback;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const mapHistoryRowToActivityItem = (
  row: ActivityHistoryItem,
  viewerRole: 'user' | 'admin'
): ActivityItem => {
  const dismissedAtTs = parseTimestamp(row.dismissed_at);
  const snapshot = row.snapshot;
  if (snapshot && typeof snapshot === 'object') {
    const payload = snapshot as Record<string, unknown>;
    if (payload.kind === 'download' && payload.download && typeof payload.download === 'object') {
      const statusKey = row.final_status === 'error' || row.final_status === 'cancelled'
        ? row.final_status
        : 'complete';
      const downloadItem = downloadToActivityItem(payload.download as Book, statusKey);
      const requestPayload = payload.request;
      if (requestPayload && typeof requestPayload === 'object') {
        const requestRecord = requestPayload as RequestRecord;
        return {
          ...downloadItem,
          id: `history-${row.id}`,
          timestamp: dismissedAtTs || downloadItem.timestamp,
          requestId: requestRecord.id,
          requestLevel: requestRecord.request_level,
          requestNote: requestRecord.note || undefined,
          requestRecord,
          adminNote: requestRecord.admin_note || undefined,
          username: requestRecord.username || downloadItem.username,
        };
      }

      return {
        ...downloadItem,
        id: `history-${row.id}`,
        timestamp: dismissedAtTs || downloadItem.timestamp,
      };
    }

    if (payload.kind === 'request' && payload.request && typeof payload.request === 'object') {
      const requestItem = requestToActivityItem(payload.request as RequestRecord, viewerRole);
      return {
        ...requestItem,
        id: `history-${row.id}`,
        timestamp: dismissedAtTs || requestItem.timestamp,
      };
    }
  }

  const visualStatus: ActivityItem['visualStatus'] =
    row.final_status === 'error'
      ? 'error'
      : row.final_status === 'cancelled'
        ? 'cancelled'
        : row.final_status === 'rejected'
          ? 'rejected'
          : 'complete';
  const statusLabel =
    visualStatus === 'error'
      ? 'Failed'
      : visualStatus === 'cancelled'
        ? 'Cancelled'
        : visualStatus === 'rejected'
          ? viewerRole === 'admin'
            ? 'Declined'
            : 'Not approved'
          : 'Complete';

  return {
    id: `history-${row.id}`,
    kind: row.item_type === 'request' ? 'request' : 'download',
    visualStatus,
    title: row.item_type === 'request' ? 'Request' : 'Download',
    author: '',
    metaLine: row.item_key,
    statusLabel,
    timestamp: dismissedAtTs,
  };
};

interface UseActivityParams {
  isAuthenticated: boolean;
  isAdmin: boolean;
  showToast: (
    message: string,
    type?: 'info' | 'success' | 'error',
    persistent?: boolean
  ) => string;
  socket: Socket | null;
}

interface UseActivityResult {
  activityStatus: StatusData;
  requestItems: ActivityItem[];
  dismissedActivityKeys: string[];
  historyItems: ActivityItem[];
  pendingRequestCount: number;
  isActivitySnapshotLoading: boolean;
  activityHistoryLoading: boolean;
  activityHistoryHasMore: boolean;
  refreshActivitySnapshot: () => Promise<void>;
  handleActivityTabChange: (tab: 'all' | 'downloads' | 'requests' | 'history') => void;
  resetActivity: () => void;
  handleActivityHistoryLoadMore: () => void;
  handleRequestDismiss: (requestId: number) => void;
  handleDownloadDismiss: (bookId: string, linkedRequestId?: number) => void;
  handleClearCompleted: (items: ActivityDismissTarget[]) => void;
  handleClearHistory: () => void;
}

export const useActivity = ({
  isAuthenticated,
  isAdmin,
  showToast,
  socket,
}: UseActivityParams): UseActivityResult => {
  const [activityStatus, setActivityStatus] = useState<StatusData>({});
  const [activityRequests, setActivityRequests] = useState<RequestRecord[]>([]);
  const [dismissedActivityKeys, setDismissedActivityKeys] = useState<string[]>([]);
  const [isActivitySnapshotLoading, setIsActivitySnapshotLoading] = useState(false);

  const [activityHistoryRows, setActivityHistoryRows] = useState<ActivityHistoryItem[]>([]);
  const [activityHistoryOffset, setActivityHistoryOffset] = useState(0);
  const [activityHistoryHasMore, setActivityHistoryHasMore] = useState(false);
  const [activityHistoryLoading, setActivityHistoryLoading] = useState(false);
  const [activityHistoryLoaded, setActivityHistoryLoaded] = useState(false);

  const resetActivityHistory = useCallback(() => {
    setActivityHistoryRows([]);
    setActivityHistoryOffset(0);
    setActivityHistoryHasMore(false);
    setActivityHistoryLoaded(false);
  }, []);

  const resetActivity = useCallback(() => {
    setActivityStatus({});
    setActivityRequests([]);
    setDismissedActivityKeys([]);
    resetActivityHistory();
  }, [resetActivityHistory]);

  const refreshActivitySnapshot = useCallback(async () => {
    if (!isAuthenticated) {
      resetActivity();
      return;
    }

    setIsActivitySnapshotLoading(true);
    try {
      const snapshot = await getActivitySnapshot();
      setActivityStatus(snapshot.status || {});
      setActivityRequests(Array.isArray(snapshot.requests) ? snapshot.requests : []);
      const keys = Array.isArray(snapshot.dismissed)
        ? snapshot.dismissed
            .map((entry) => entry.item_key)
            .filter((key): key is string => typeof key === 'string' && key.trim().length > 0)
        : [];
      setDismissedActivityKeys(Array.from(new Set(keys)));
    } catch (error) {
      console.warn('Failed to refresh activity snapshot:', error);
    } finally {
      setIsActivitySnapshotLoading(false);
    }
  }, [isAuthenticated, resetActivity]);

  const refreshActivityHistory = useCallback(async () => {
    if (!isAuthenticated) {
      resetActivityHistory();
      return;
    }

    setActivityHistoryLoading(true);
    try {
      const rows = await listActivityHistory(HISTORY_PAGE_SIZE, 0);
      const normalizedRows = Array.isArray(rows) ? rows : [];
      setActivityHistoryRows(normalizedRows);
      setActivityHistoryOffset(normalizedRows.length);
      setActivityHistoryHasMore(normalizedRows.length === HISTORY_PAGE_SIZE);
      setActivityHistoryLoaded(true);
    } catch (error) {
      console.warn('Failed to refresh activity history:', error);
    } finally {
      setActivityHistoryLoading(false);
    }
  }, [isAuthenticated, resetActivityHistory]);

  const handleActivityTabChange = useCallback((tab: 'all' | 'downloads' | 'requests' | 'history') => {
    if (tab !== 'history' || activityHistoryLoaded || activityHistoryLoading) {
      return;
    }
    void refreshActivityHistory();
  }, [activityHistoryLoaded, activityHistoryLoading, refreshActivityHistory]);

  const handleActivityHistoryLoadMore = useCallback(() => {
    if (!isAuthenticated || activityHistoryLoading || !activityHistoryHasMore) {
      return;
    }

    setActivityHistoryLoading(true);
    void listActivityHistory(HISTORY_PAGE_SIZE, activityHistoryOffset)
      .then((rows) => {
        const normalizedRows = Array.isArray(rows) ? rows : [];
        setActivityHistoryRows((current) => {
          const existingIds = new Set(current.map((row) => row.id));
          const nextRows = normalizedRows.filter((row) => !existingIds.has(row.id));
          return [...current, ...nextRows];
        });
        setActivityHistoryOffset((current) => current + normalizedRows.length);
        setActivityHistoryHasMore(normalizedRows.length === HISTORY_PAGE_SIZE);
      })
      .catch((error) => {
        console.warn('Failed to load more activity history:', error);
      })
      .finally(() => {
        setActivityHistoryLoading(false);
      });
  }, [activityHistoryHasMore, activityHistoryLoading, activityHistoryOffset, isAuthenticated]);

  useEffect(() => {
    void refreshActivitySnapshot();
  }, [refreshActivitySnapshot]);

  useEffect(() => {
    if (!socket || !isAuthenticated) {
      return;
    }

    const handleActivityUpdate = () => {
      void refreshActivitySnapshot();
      if (activityHistoryLoaded) {
        void refreshActivityHistory();
      }
    };

    socket.on('activity_update', handleActivityUpdate);
    return () => {
      socket.off('activity_update', handleActivityUpdate);
    };
  }, [activityHistoryLoaded, isAuthenticated, refreshActivitySnapshot, refreshActivityHistory, socket]);

  const requestItems = useMemo(
    () =>
      activityRequests
        .map((record) => requestToActivityItem(record, isAdmin ? 'admin' : 'user'))
        .sort((left, right) => right.timestamp - left.timestamp),
    [activityRequests, isAdmin]
  );

  const historyItems = useMemo(
    () =>
      activityHistoryRows
        .map((row) => mapHistoryRowToActivityItem(row, isAdmin ? 'admin' : 'user'))
        .sort((left, right) => right.timestamp - left.timestamp),
    [activityHistoryRows, isAdmin]
  );

  const pendingRequestCount = useMemo(
    () => activityRequests.filter((record) => record.status === 'pending').length,
    [activityRequests]
  );

  const refreshHistoryIfLoaded = useCallback(() => {
    if (!activityHistoryLoaded) {
      return;
    }
    void refreshActivityHistory();
  }, [activityHistoryLoaded, refreshActivityHistory]);

  const dismissItems = useCallback((items: ActivityDismissPayload[], optimisticKeys: string[], errorMessage: string) => {
    setDismissedActivityKeys((current) => Array.from(new Set([...current, ...optimisticKeys])));
    void dismissManyActivityItems(items)
      .then(() => {
        void refreshActivitySnapshot();
        refreshHistoryIfLoaded();
      })
      .catch((error) => {
        console.error('Activity dismiss failed:', error);
        void refreshActivitySnapshot();
        refreshHistoryIfLoaded();
        showToast(errorMessage, 'error');
      });
  }, [refreshActivitySnapshot, refreshHistoryIfLoaded, showToast]);

  const handleRequestDismiss = useCallback((requestId: number) => {
    const requestKey = `request:${requestId}`;
    setDismissedActivityKeys((current) =>
      current.includes(requestKey) ? current : [...current, requestKey]
    );

    void dismissActivityItem({
      item_type: 'request',
      item_key: requestKey,
    }).then(() => {
      void refreshActivitySnapshot();
      refreshHistoryIfLoaded();
    }).catch((error) => {
      console.error('Request dismiss failed:', error);
      void refreshActivitySnapshot();
      refreshHistoryIfLoaded();
      showToast('Failed to clear request', 'error');
    });
  }, [refreshActivitySnapshot, refreshHistoryIfLoaded, showToast]);

  const handleDownloadDismiss = useCallback((bookId: string, linkedRequestId?: number) => {
    const items: ActivityDismissTarget[] = [{ itemType: 'download', itemKey: `download:${bookId}` }];
    if (typeof linkedRequestId === 'number' && Number.isFinite(linkedRequestId)) {
      items.push({ itemType: 'request', itemKey: `request:${linkedRequestId}` });
    }

    dismissItems(
      items.map((item) => ({
        item_type: item.itemType,
        item_key: item.itemKey,
      })),
      items.map((item) => item.itemKey),
      'Failed to clear item'
    );
  }, [dismissItems]);

  const handleClearCompleted = useCallback((items: ActivityDismissTarget[]) => {
    if (!items.length) {
      return;
    }

    dismissItems(
      items.map((item) => ({
        item_type: item.itemType,
        item_key: item.itemKey,
      })),
      Array.from(new Set(items.map((item) => item.itemKey))),
      'Failed to clear finished downloads'
    );
  }, [dismissItems]);

  const handleClearHistory = useCallback(() => {
    resetActivityHistory();
    void clearActivityHistory().catch((error) => {
      console.error('Clear history failed:', error);
      void refreshActivityHistory();
      showToast('Failed to clear history', 'error');
    });
  }, [refreshActivityHistory, resetActivityHistory, showToast]);

  return {
    activityStatus,
    requestItems,
    dismissedActivityKeys,
    historyItems,
    pendingRequestCount,
    isActivitySnapshotLoading,
    activityHistoryLoading,
    activityHistoryHasMore,
    refreshActivitySnapshot,
    handleActivityTabChange,
    resetActivity,
    handleActivityHistoryLoadMore,
    handleRequestDismiss,
    handleDownloadDismiss,
    handleClearCompleted,
    handleClearHistory,
  };
};
