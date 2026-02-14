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
import { PerUserSettings } from './types';

interface UseUsersFetchParams {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

export interface UserEditContext {
  user: AdminUser;
  downloadDefaults: DownloadDefaults;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  userSettings: PerUserSettings;
  userOverridableSettings: Set<string>;
}

export const useUsersFetch = ({ onShowToast }: UseUsersFetchParams) => {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const shouldSuppressAccessToast = (message: string): boolean =>
    message.toLowerCase().includes('admin access required');

  const fetchUsers = useCallback(async (): Promise<AdminUser[]> => {
    try {
      setLoading(true);
      setLoadError(null);
      const data = await getAdminUsers();
      setUsers(data);
      return data;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load users';
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
      const usersOverridableKeys = usersTab.fields
        .filter((field) => field.type !== 'HeadingField' && (field as { userOverridable?: boolean }).userOverridable)
        .map((field) => field.key);
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
