import { useCallback, useEffect, useState } from 'react';
import {
  AdminUser,
  DeliveryPreferencesResponse,
  DownloadDefaults,
  getAdminDeliveryPreferences,
  getAdminUser,
  getAdminUsers,
  getDownloadDefaults,
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

  const fetchUsers = useCallback(async () => {
    try {
      setLoading(true);
      setLoadError(null);
      const data = await getAdminUsers();
      setUsers(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load users';
      setLoadError(message);
      onShowToast?.(message, 'error');
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
