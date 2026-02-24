import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  cancelRequest as cancelUserRequest,
  fulfilAdminRequest,
  isApiResponseError,
  listAdminRequests,
  listRequests,
  rejectAdminRequest,
} from '../services/api';
import { RequestRecord } from '../types';
import { useSocket } from '../contexts/SocketContext';
import {
  applyRequestUpdateEvent,
  normalizeRequestUpdatePayload,
  upsertRequestRecord,
} from './useRequests.helpers';
import type { RequestUpdateEventPayload } from './useRequests.helpers';

interface UseRequestsOptions {
  isAdmin: boolean;
  enabled: boolean;
  pollIntervalMs?: number;
}

export interface UseRequestsReturn {
  requests: RequestRecord[];
  pendingCount: number;
  isLoading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  cancelRequest: (id: number) => Promise<void>;
  fulfilRequest: (
    id: number,
    releaseData?: Record<string, unknown>,
    adminNote?: string,
    manualApproval?: boolean
  ) => Promise<void>;
  rejectRequest: (id: number, adminNote?: string) => Promise<void>;
}

const toErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

const shouldFallbackToUserRequestList = (error: unknown): boolean => {
  return isApiResponseError(error) && (error.status === 401 || error.status === 403);
};

export const useRequests = ({
  isAdmin,
  enabled,
  pollIntervalMs = 10_000,
}: UseRequestsOptions): UseRequestsReturn => {
  const { socket, connected } = useSocket();
  const [requests, setRequests] = useState<RequestRecord[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const requestsRef = useRef<RequestRecord[]>([]);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    requestsRef.current = requests;
  }, [requests]);

  const refresh = useCallback(async () => {
    if (!enabled) {
      return;
    }

    setIsLoading(true);
    try {
      let rows: RequestRecord[];
      if (isAdmin) {
        try {
          rows = await listAdminRequests();
        } catch (err) {
          // Role/session state can momentarily desync between tabs.
          // If admin list is unauthorized, fall back to user-scoped list.
          if (shouldFallbackToUserRequestList(err)) {
            rows = await listRequests();
          } else {
            throw err;
          }
        }
      } else {
        rows = await listRequests();
      }
      setRequests(rows);
      setError(null);
    } catch (err) {
      setError(toErrorMessage(err, 'Failed to load requests'));
    } finally {
      setIsLoading(false);
    }
  }, [enabled, isAdmin]);

  const startPolling = useCallback(() => {
    if (pollIntervalRef.current || !enabled) {
      return;
    }
    pollIntervalRef.current = setInterval(() => {
      void refresh();
    }, pollIntervalMs);
  }, [enabled, refresh, pollIntervalMs]);

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setRequests([]);
      setError(null);
      setIsLoading(false);
      stopPolling();
      return;
    }

    void refresh();
  }, [enabled, refresh, stopPolling]);

  useEffect(() => {
    if (!enabled) {
      stopPolling();
      return;
    }

    if (!socket) {
      startPolling();
      return;
    }

    const handleNewRequest = () => {
      void refresh();
    };

    const handleRequestUpdate = (rawPayload: unknown) => {
      const payload = normalizeRequestUpdatePayload(rawPayload);
      if (!payload) {
        void refresh();
        return;
      }

      let found = false;
      setRequests((prev) => {
        const result = applyRequestUpdateEvent(prev, payload);
        found = result.found;
        return result.records;
      });

      if (!found) {
        void refresh();
        return;
      }

      // Ensure we pick up full record updates (e.g. admin_note) after status transitions.
      void refresh();
    };

    socket.on('new_request', handleNewRequest);
    socket.on('request_update', handleRequestUpdate);

    if (connected) {
      stopPolling();
    } else {
      startPolling();
    }

    return () => {
      socket.off('new_request', handleNewRequest);
      socket.off('request_update', handleRequestUpdate);
    };
  }, [enabled, socket, connected, refresh, startPolling, stopPolling]);

  useEffect(() => {
    if (!enabled) {
      stopPolling();
      return;
    }

    if (connected) {
      stopPolling();
    } else {
      startPolling();
    }
  }, [enabled, connected, startPolling, stopPolling]);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  const cancelRequest = useCallback(async (id: number) => {
    const previous = requestsRef.current;
    setRequests((prev) => {
      const result = applyRequestUpdateEvent(prev, { request_id: id, status: 'cancelled' });
      return result.records;
    });

    try {
      const updated = await cancelUserRequest(id);
      setRequests((prev) => upsertRequestRecord(prev, updated));
      setError(null);
    } catch (err) {
      setRequests(previous);
      const message = toErrorMessage(err, 'Failed to cancel request');
      setError(message);
      throw new Error(message);
    }
  }, []);

  const fulfilRequest = useCallback(
    async (
      id: number,
      releaseData?: Record<string, unknown>,
      adminNote?: string,
      manualApproval?: boolean
    ) => {
      if (!isAdmin) {
        throw new Error('Admin access required');
      }

      const previous = requestsRef.current;
      setRequests((prev) => {
        const result = applyRequestUpdateEvent(prev, { request_id: id, status: 'fulfilled' });
        return result.records;
      });

      try {
        const updated = await fulfilAdminRequest(id, {
          release_data: releaseData,
          admin_note: adminNote,
          manual_approval: manualApproval,
        });
        setRequests((prev) => upsertRequestRecord(prev, updated));
        setError(null);
      } catch (err) {
        setRequests(previous);
        const message = toErrorMessage(err, 'Failed to fulfil request');
        setError(message);
        throw new Error(message);
      }
    },
    [isAdmin]
  );

  const rejectRequest = useCallback(
    async (id: number, adminNote?: string) => {
      if (!isAdmin) {
        throw new Error('Admin access required');
      }

      const previous = requestsRef.current;
      setRequests((prev) => {
        const result = applyRequestUpdateEvent(prev, { request_id: id, status: 'rejected' });
        return result.records;
      });

      try {
        const updated = await rejectAdminRequest(id, {
          admin_note: adminNote,
        });
        setRequests((prev) => upsertRequestRecord(prev, updated));
        setError(null);
      } catch (err) {
        setRequests(previous);
        const message = toErrorMessage(err, 'Failed to reject request');
        setError(message);
        throw new Error(message);
      }
    },
    [isAdmin]
  );

  const pendingCount = useMemo(
    () => requests.filter((record) => record.status === 'pending').length,
    [requests]
  );

  return {
    requests,
    pendingCount,
    isLoading,
    error,
    refresh,
    cancelRequest,
    fulfilRequest,
    rejectRequest,
  };
};

export type { RequestUpdateEventPayload };
export { upsertRequestRecord, applyRequestUpdateEvent };
