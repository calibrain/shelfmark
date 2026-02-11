import { useCallback, useEffect, useState } from 'react';
import {
  AdminUser,
  BookloreOption,
  DownloadDefaults,
  createAdminUser,
  deleteAdminUser,
  getAdminUser,
  getAdminUsers,
  getBookloreOptions,
  getDownloadDefaults,
  getSettingsTab,
  updateAdminUser,
} from '../../../services/api';
import {
  EMPTY_OVERRIDES,
  FALLBACK_OVERRIDABLE_SETTINGS,
  INITIAL_CREATE_FORM,
  OVERRIDE_KEY_TO_SETTING_KEY,
  CreateUserFormState,
  OverrideKey,
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
  const [userSettings, setUserSettings] = useState<PerUserSettings>({});
  const [overrides, setOverrides] = useState<Record<OverrideKey, boolean>>({ ...EMPTY_OVERRIDES });
  const [userOverridableSettings, setUserOverridableSettings] = useState<Set<keyof PerUserSettings>>(
    new Set(FALLBACK_OVERRIDABLE_SETTINGS)
  );
  const [bookloreLibraries, setBookloreLibraries] = useState<BookloreOption[]>([]);
  const [booklorePaths, setBooklorePaths] = useState<BookloreOption[]>([]);

  const isUserOverridable = useCallback(
    (key: keyof PerUserSettings) => userOverridableSettings.has(key),
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
    setUserSettings({});
    setOverrides({ ...EMPTY_OVERRIDES });
    setUserOverridableSettings(new Set(FALLBACK_OVERRIDABLE_SETTINGS));
    setBookloreLibraries([]);
    setBooklorePaths([]);
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
      const settings = (fullUser.settings || {}) as PerUserSettings;
      setUserSettings(settings);

      let overridableSettings = new Set<keyof PerUserSettings>(FALLBACK_OVERRIDABLE_SETTINGS);
      try {
        const downloadsTab = await getSettingsTab('downloads');
        const fromMetadata = new Set<keyof PerUserSettings>();
        downloadsTab.fields.forEach((field) => {
          if ('userOverridable' in field && field.userOverridable === true) {
            const key = field.key as keyof PerUserSettings;
            if (FALLBACK_OVERRIDABLE_SETTINGS.has(key)) {
              fromMetadata.add(key);
            }
          }
        });
        if (fromMetadata.size > 0) {
          overridableSettings = fromMetadata;
        }
      } catch {
        // Keep fallback behavior if settings metadata fails to load.
      }
      setUserOverridableSettings(overridableSettings);

      if (defaults.BOOKS_OUTPUT_MODE === 'booklore') {
        try {
          const blOptions = await getBookloreOptions();
          setBookloreLibraries(blOptions.libraries || []);
          setBooklorePaths(blOptions.paths || []);
        } catch {
          setBookloreLibraries([]);
          setBooklorePaths([]);
        }
      }

      setOverrides({
        destination: overridableSettings.has('DESTINATION') && !!settings.DESTINATION,
        booklore_library_id: overridableSettings.has('BOOKLORE_LIBRARY_ID') && !!settings.BOOKLORE_LIBRARY_ID,
        booklore_path_id: overridableSettings.has('BOOKLORE_PATH_ID') && !!settings.BOOKLORE_PATH_ID,
        email_recipients: overridableSettings.has('EMAIL_RECIPIENTS') && !!settings.EMAIL_RECIPIENTS?.length,
      });
    } catch {
      setDownloadDefaults(null);
      setUserSettings({});
      setOverrides({ ...EMPTY_OVERRIDES });
      setUserOverridableSettings(new Set(FALLBACK_OVERRIDABLE_SETTINGS));
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

    const settingsPayload: Record<string, unknown> = {};
    if (isUserOverridable('DESTINATION')) {
      settingsPayload.DESTINATION = overrides.destination ? (userSettings.DESTINATION || '') : null;
    }
    if (isUserOverridable('BOOKLORE_LIBRARY_ID')) {
      settingsPayload.BOOKLORE_LIBRARY_ID = overrides.booklore_library_id ? (userSettings.BOOKLORE_LIBRARY_ID || '') : null;
    }
    if (isUserOverridable('BOOKLORE_PATH_ID')) {
      settingsPayload.BOOKLORE_PATH_ID = overrides.booklore_path_id ? (userSettings.BOOKLORE_PATH_ID || '') : null;
    }
    if (isUserOverridable('EMAIL_RECIPIENTS')) {
      settingsPayload.EMAIL_RECIPIENTS = overrides.email_recipients ? (userSettings.EMAIL_RECIPIENTS || []) : null;
    }

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
    editPassword,
    editPasswordConfirm,
    editingUser,
    fetchUsers,
    isUserOverridable,
    onShowToast,
    overrides.booklore_library_id,
    overrides.booklore_path_id,
    overrides.destination,
    overrides.email_recipients,
    userSettings.BOOKLORE_LIBRARY_ID,
    userSettings.BOOKLORE_PATH_ID,
    userSettings.DESTINATION,
    userSettings.EMAIL_RECIPIENTS,
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
      onShowToast?.(`User ${data.username} created`, 'success');
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

  const toggleOverride = useCallback((key: OverrideKey, enabled: boolean) => {
    setOverrides((prev) => ({ ...prev, [key]: enabled }));
    if (!enabled) {
      setUserSettings((prev) => {
        const next = { ...prev };
        const settingKey = OVERRIDE_KEY_TO_SETTING_KEY[key];
        (next as Record<string, unknown>)[settingKey] = null;
        return next;
      });
    }
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
    userSettings,
    setUserSettings,
    overrides,
    toggleOverride,
    userOverridableSettings,
    isUserOverridable,
    bookloreLibraries,
    booklorePaths,
  };
};
