import { useCallback, useEffect, useState } from 'react';
import {
  AdminUser,
  DeliveryPreferencesResponse,
  DownloadDefaults,
  createAdminUser,
  deleteAdminUser,
  getAdminDeliveryPreferences,
  getAdminUser,
  getAdminUsers,
  getDownloadDefaults,
  updateAdminUser,
} from '../../../services/api';
import {
  INITIAL_CREATE_FORM,
  CreateUserFormState,
  PerUserSettings,
  getUserEditCapabilities,
} from './types';

interface UseUsersDataParams {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

export const useUsersData = ({ onShowToast }: UseUsersDataParams) => {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [createForm, setCreateForm] = useState<CreateUserFormState>({ ...INITIAL_CREATE_FORM });
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null);

  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [editPassword, setEditPassword] = useState('');
  const [editPasswordConfirm, setEditPasswordConfirm] = useState('');
  const [downloadDefaults, setDownloadDefaults] = useState<DownloadDefaults | null>(null);
  const [deliveryPreferences, setDeliveryPreferences] = useState<DeliveryPreferencesResponse | null>(null);
  const [userSettings, setUserSettings] = useState<PerUserSettings>({});
  const [userOverridableSettings, setUserOverridableSettings] = useState<Set<string>>(new Set());

  const isUserOverridable = useCallback(
    (key: keyof PerUserSettings) => userOverridableSettings.has(String(key)),
    [userOverridableSettings]
  );

  const fetchUsers = useCallback(async () => {
    try {
      setLoading(true);
      setLoadError(null);
      const data = await getAdminUsers();
      setUsers(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load users';
      setLoadError(msg);
      onShowToast?.(msg, 'error');
    } finally {
      setLoading(false);
    }
  }, [onShowToast]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const clearEditState = useCallback(() => {
    setSaving(false);
    setEditingUser(null);
    setEditPassword('');
    setEditPasswordConfirm('');
    setDeliveryPreferences(null);
    setUserSettings({});
    setUserOverridableSettings(new Set());
  }, []);

  const startEditing = useCallback(async (user: AdminUser) => {
    setEditingUser({ ...user });
    setEditPassword('');
    setEditPasswordConfirm('');

    try {
      const [fullUser, defaults] = await Promise.all([
        getAdminUser(user.id),
        getDownloadDefaults(),
      ]);
      setDownloadDefaults(defaults);
      setUserSettings((fullUser.settings || {}) as PerUserSettings);

      try {
        const preferences = await getAdminDeliveryPreferences(user.id);
        setDeliveryPreferences(preferences);
        setUserSettings((preferences.userOverrides || fullUser.settings || {}) as PerUserSettings);
        setUserOverridableSettings(new Set(preferences.keys || []));
      } catch {
        setDeliveryPreferences(null);
        setUserOverridableSettings(new Set());
      }
    } catch {
      setDownloadDefaults(null);
      setDeliveryPreferences(null);
      setUserSettings({});
      setUserOverridableSettings(new Set());
    }
  }, []);

  const deleteUser = useCallback(async (userId: number): Promise<boolean> => {
    setDeletingUserId(userId);
    try {
      await deleteAdminUser(userId);
      onShowToast?.('User deleted', 'success');
      await fetchUsers();
      return true;
    } catch {
      onShowToast?.('Failed to delete user', 'error');
      return false;
    } finally {
      setDeletingUserId(null);
    }
  }, [fetchUsers, onShowToast]);

  const saveEditedUser = useCallback(async (): Promise<boolean> => {
    if (!editingUser) return false;

    if (editPassword) {
      if (editPassword.length < 4) {
        onShowToast?.('Password must be at least 4 characters', 'error');
        return false;
      }
      if (editPassword !== editPasswordConfirm) {
        onShowToast?.('Passwords do not match', 'error');
        return false;
      }
    }

    const hasOverride = (key: keyof PerUserSettings): boolean =>
      Object.prototype.hasOwnProperty.call(userSettings, key) &&
      userSettings[key] !== null &&
      userSettings[key] !== undefined;

    const settingsPayload: Record<string, unknown> = {};
    const overrideKeys = deliveryPreferences?.keys || Array.from(userOverridableSettings);
    overrideKeys.forEach((key) => {
      const typedKey = key as keyof PerUserSettings;
      settingsPayload[key] = hasOverride(typedKey)
        ? (userSettings[typedKey] ?? '')
        : null;
    });

    const capabilities = getUserEditCapabilities(
      editingUser,
      downloadDefaults?.OIDC_USE_ADMIN_GROUP
    );

    setSaving(true);
    try {
      await updateAdminUser(editingUser.id, {
        ...(capabilities.canEditEmail ? { email: editingUser.email } : {}),
        ...(capabilities.canEditDisplayName ? { display_name: editingUser.display_name } : {}),
        ...(capabilities.canEditRole ? { role: editingUser.role } : {}),
        ...(capabilities.canSetPassword && editPassword ? { password: editPassword } : {}),
        ...(Object.keys(settingsPayload).length ? { settings: settingsPayload } : {}),
      });
      clearEditState();
      onShowToast?.('User updated', 'success');
      await fetchUsers();
      return true;
    } catch {
      onShowToast?.('Failed to update user', 'error');
      return false;
    } finally {
      setSaving(false);
    }
  }, [
    clearEditState,
    downloadDefaults?.OIDC_USE_ADMIN_GROUP,
    deliveryPreferences?.keys,
    editPassword,
    editPasswordConfirm,
    editingUser,
    fetchUsers,
    onShowToast,
    userOverridableSettings,
    userSettings,
  ]);

  const createUser = useCallback(async (): Promise<boolean> => {
    if (!createForm.username || !createForm.password) {
      onShowToast?.('Username and password are required', 'error');
      return false;
    }
    if (createForm.password.length < 4) {
      onShowToast?.('Password must be at least 4 characters', 'error');
      return false;
    }
    setCreating(true);
    try {
      const data = await createAdminUser({
        username: createForm.username,
        password: createForm.password,
        email: createForm.email || undefined,
        display_name: createForm.display_name || undefined,
        role: createForm.role || undefined,
      });
      setCreateForm({ ...INITIAL_CREATE_FORM });
      onShowToast?.(`Local user ${data.username} created`, 'success');
      await fetchUsers();
      return true;
    } catch (err) {
      onShowToast?.((err as Error).message || 'Failed to create user', 'error');
      return false;
    } finally {
      setCreating(false);
    }
  }, [createForm, fetchUsers, onShowToast]);

  const resetCreateForm = useCallback(() => {
    setCreateForm({ ...INITIAL_CREATE_FORM });
  }, []);

  return {
    users,
    loading,
    loadError,
    fetchUsers,
    createForm,
    setCreateForm,
    creating,
    saving,
    deletingUserId,
    createUser,
    resetCreateForm,
    editingUser,
    setEditingUser,
    startEditing,
    clearEditState,
    saveEditedUser,
    deleteUser,
    editPassword,
    setEditPassword,
    editPasswordConfirm,
    setEditPasswordConfirm,
    downloadDefaults,
    deliveryPreferences,
    userSettings,
    setUserSettings,
    userOverridableSettings,
    isUserOverridable,
  };
};
