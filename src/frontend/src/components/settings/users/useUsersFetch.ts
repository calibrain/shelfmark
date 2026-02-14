import { useCallback, useEffect, useState } from 'react';
import {
  AdminUser,
  DeliveryPreferencesResponse,
  DownloadDefaults,
  getAdminDeliveryPreferences,
  getAdminUser,
  getAdminUsers,
  getDownloadDefaults,
  getSettingsTab,
} from '../../../services/api';
import { SettingsField } from '../../../types/settings';
import { PerUserSettings } from './types';

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

const loadUsersIntoCache = async (): Promise<AdminUser[]> => {
  if (cachedUsers !== null) {
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

export interface UserEditContext {
  user: AdminUser;
  downloadDefaults: DownloadDefaults;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  userSettings: PerUserSettings;
  userOverridableSettings: Set<string>;
}

const getUserOverridableKeys = (fields: SettingsField[]): string[] => {
  const keys: string[] = [];
  const seen = new Set<string>();

  const collect = (candidateFields: SettingsField[]) => {
    candidateFields.forEach((field) => {
      if (field.type === 'CustomComponentField') {
        if (field.boundFields && field.boundFields.length > 0) {
          collect(field.boundFields);
        }
        return;
      }

      if (field.type === 'HeadingField') {
        return;
      }

      if ((field as { userOverridable?: boolean }).userOverridable && !seen.has(field.key)) {
        seen.add(field.key);
        keys.push(field.key);
      }
    });
  };

  collect(fields);
  return keys;
};

export const useUsersFetch = ({ onShowToast }: UseUsersFetchParams) => {
  const [users, setUsers] = useState<AdminUser[]>(() => cachedUsers ?? []);
  const [loading, setLoading] = useState<boolean>(() => cachedUsers === null);
  const [loadError, setLoadError] = useState<string | null>(() => cachedLoadError);

  const fetchUsers = useCallback(async (): Promise<AdminUser[]> => {
    const hasCachedResult = cachedUsers !== null;
    try {
      if (!hasCachedResult) {
        setLoading(true);
      }
      setLoadError(null);
      const data = await loadUsersIntoCache();
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
  }, [onShowToast]);

  useEffect(() => {
    void fetchUsers();
  }, [fetchUsers]);

  const fetchUserEditContext = useCallback(async (userId: number): Promise<UserEditContext> => {
    const [fullUser, defaults] = await Promise.all([
      getAdminUser(userId),
      getDownloadDefaults(),
    ]);

    let deliveryPreferences: DeliveryPreferencesResponse | null = null;
    let userSettings = (fullUser.settings || {}) as PerUserSettings;
    let userOverridableSettings = new Set<string>();

    try {
      const preferences = await getAdminDeliveryPreferences(userId);
      deliveryPreferences = preferences;
      userSettings = (preferences.userOverrides || fullUser.settings || {}) as PerUserSettings;
      userOverridableSettings = new Set(preferences.keys || []);
    } catch {
      // Delivery preference introspection is best-effort.
    }

    try {
      const usersTab = await getSettingsTab('users');
      const usersOverridableKeys = getUserOverridableKeys(usersTab.fields);
      usersOverridableKeys.forEach((key) => userOverridableSettings.add(key));
    } catch {
      // Users-tab metadata is best-effort; save still validates server-side.
    }

    return {
      user: fullUser,
      downloadDefaults: defaults,
      deliveryPreferences,
      userSettings,
      userOverridableSettings,
    };
  }, []);

  return {
    users,
    loading,
    loadError,
    fetchUsers,
    fetchUserEditContext,
  };
};
