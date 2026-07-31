import { useCallback } from 'react';

import {
  cancelRequest as cancelUserRequest,
  fulfilAdminBookRequests,
  rejectAdminRequest,
} from '../services/api';

interface UseRequestsOptions {
  isAdmin: boolean;
}

interface UseRequestsReturn {
  cancelRequest: (id: number) => Promise<void>;
  fulfilBookRequests: (
    bookId: number,
    releaseData: Record<string, unknown>,
    adminNote?: string,
  ) => Promise<void>;
  rejectRequest: (id: number, adminNote?: string) => Promise<void>;
}

const toErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

export const useRequests = ({ isAdmin }: UseRequestsOptions): UseRequestsReturn => {
  const cancelRequest = useCallback(async (id: number) => {
    try {
      await cancelUserRequest(id);
    } catch (err) {
      const message = toErrorMessage(err, 'Failed to cancel request');
      throw new Error(message, { cause: err });
    }
  }, []);

  const fulfilBookRequests = useCallback(
    async (bookId: number, releaseData: Record<string, unknown>, adminNote?: string) => {
      if (!isAdmin) {
        throw new Error('Admin access required');
      }

      try {
        await fulfilAdminBookRequests(bookId, {
          release_data: releaseData,
          admin_note: adminNote,
        });
      } catch (err) {
        const message = toErrorMessage(err, 'Failed to fulfil request');
        throw new Error(message, { cause: err });
      }
    },
    [isAdmin],
  );

  const rejectRequest = useCallback(
    async (id: number, adminNote?: string) => {
      if (!isAdmin) {
        throw new Error('Admin access required');
      }

      try {
        await rejectAdminRequest(id, {
          admin_note: adminNote,
        });
      } catch (err) {
        const message = toErrorMessage(err, 'Failed to reject request');
        throw new Error(message, { cause: err });
      }
    },
    [isAdmin],
  );

  return {
    cancelRequest,
    fulfilBookRequests,
    rejectRequest,
  };
};
