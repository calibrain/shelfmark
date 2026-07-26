import { useCallback, useState } from 'react';

import type { AdminUser } from '../services/api';
import { getAdminUsers } from '../services/api';
import { useMountEffect } from './useMountEffect';

interface UseUsersFetchParams {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

let cachedUsers: AdminUser[] | null = null;
let cachedLoadError: string | null = null;
let usersCacheLoadPromise: Promise<AdminUser[]> | null = null;

const shouldSuppressAccessToast = (message: string): boolean =>
  message.toLowerCase().includes('admin access required');

const toLoadErrorMessage = (err: unknown): string =>
  err instanceof Error ? err.message : 'Failed to load users';

interface LoadUsersOptions {
  force?: boolean;
}

const loadUsersIntoCache = async ({ force = false }: LoadUsersOptions = {}): Promise<
  AdminUser[]
> => {
  if (!force && cachedUsers !== null) {
    return cachedUsers;
  }
  if (usersCacheLoadPromise) {
    return usersCacheLoadPromise;
  }

  usersCacheLoadPromise = getAdminUsers()
    .then((data) => {
      cachedUsers = data;
      cachedLoadError = null;
      return data;
    })
    .finally(() => {
      usersCacheLoadPromise = null;
    });

  return usersCacheLoadPromise;
};

export const primeUsersCache = async (): Promise<void> => {
  try {
    await loadUsersIntoCache();
  } catch {
    // Silent best-effort warmup.
  }
};

export const useUsersFetch = ({ onShowToast }: UseUsersFetchParams) => {
  const [users, setUsers] = useState<AdminUser[]>(() => cachedUsers ?? []);
  const [loading, setLoading] = useState<boolean>(() => cachedUsers === null);
  const [loadError, setLoadError] = useState<string | null>(() => cachedLoadError);

  const fetchUsers = useCallback(
    async ({ force = false }: LoadUsersOptions = {}): Promise<AdminUser[]> => {
      const hasCachedResult = !force && cachedUsers !== null;
      try {
        if (!hasCachedResult) {
          setLoading(true);
        }
        setLoadError(null);
        const data = await loadUsersIntoCache({ force });
        setUsers(data);
        return data;
      } catch (err) {
        const message = toLoadErrorMessage(err);
        cachedLoadError = message;
        setLoadError(message);
        if (!shouldSuppressAccessToast(message)) {
          onShowToast?.(message, 'error');
        }
        return [];
      } finally {
        setLoading(false);
      }
    },
    [onShowToast],
  );

  useMountEffect(() => {
    void fetchUsers();
  });

  return {
    users,
    loading,
    loadError,
    fetchUsers,
  };
};
