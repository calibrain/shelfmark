import { useState } from 'react';
import {
  AdminUser,
  DeliveryPreferencesResponse,
  createAdminUser,
  deleteAdminUser,
  syncAdminCwaUsers,
  updateAdminUser,
} from '../../../services/api';
import { CreateUserFormState, PerUserSettings } from './types';

const MIN_PASSWORD_LENGTH = 4;
interface UseUserMutationsParams {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  fetchUsers: () => Promise<void>;
  createForm: CreateUserFormState;
  resetCreateForm: () => void;
  editingUser: AdminUser | null;
  editPassword: string;
  editPasswordConfirm: string;
  userSettings: PerUserSettings;
  userOverridableSettings: Set<string>;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  onEditSaveSuccess?: () => void;
}

const getPasswordError = (password: string, passwordConfirm: string) => {
  if (!password) return null;
  if (password.length < MIN_PASSWORD_LENGTH) return `Password must be at least ${MIN_PASSWORD_LENGTH} characters`;
  return password === passwordConfirm ? null : 'Passwords do not match';
};

const buildSettingsPayload = (userSettings: PerUserSettings, userOverridableSettings: Set<string>, deliveryPreferences: DeliveryPreferencesResponse | null) =>
  (deliveryPreferences?.keys || [...userOverridableSettings]).reduce<Record<string, unknown>>((payload, key) => {
    const typedKey = key as keyof PerUserSettings;
    payload[key] = Object.prototype.hasOwnProperty.call(userSettings, typedKey) && userSettings[typedKey] !== null && userSettings[typedKey] !== undefined
      ? (userSettings[typedKey] ?? '')
      : null;
    return payload;
  }, {});

export const useUserMutations = ({
  onShowToast,
  fetchUsers,
  createForm,
  resetCreateForm,
  editingUser,
  editPassword,
  editPasswordConfirm,
  userSettings,
  userOverridableSettings,
  deliveryPreferences,
  onEditSaveSuccess,
}: UseUserMutationsParams) => {
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null);
  const [syncingCwa, setSyncingCwa] = useState(false);
  const fail = (message: string) => (onShowToast?.(message, 'error'), false);

  const createUser = async () => {
    if (!createForm.username || !createForm.password) return fail('Username and password are required');
    if (createForm.password.length < MIN_PASSWORD_LENGTH) return fail(`Password must be at least ${MIN_PASSWORD_LENGTH} characters`);

    setCreating(true);
    try {
      const created = await createAdminUser({
        username: createForm.username,
        password: createForm.password,
        email: createForm.email || undefined,
        display_name: createForm.display_name || undefined,
        role: createForm.role || undefined,
      });
      resetCreateForm();
      onShowToast?.(`Local user ${created.username} created`, 'success');
      await fetchUsers();
      return true;
    } catch (err) {
      return fail(err instanceof Error ? err.message : 'Failed to create user');
    } finally {
      setCreating(false);
    }
  };

  const saveEditedUser = async () => {
    if (!editingUser) return false;
    const passwordError = getPasswordError(editPassword, editPasswordConfirm);
    if (passwordError) return fail(passwordError);

    const caps = editingUser.edit_capabilities;
    const settingsPayload = buildSettingsPayload(userSettings, userOverridableSettings, deliveryPreferences);

    setSaving(true);
    try {
      await updateAdminUser(editingUser.id, {
        ...(caps.canEditEmail ? { email: editingUser.email } : {}),
        ...(caps.canEditDisplayName ? { display_name: editingUser.display_name } : {}),
        ...(caps.canEditRole ? { role: editingUser.role } : {}),
        ...(caps.canSetPassword && editPassword ? { password: editPassword } : {}),
        ...(Object.keys(settingsPayload).length > 0 ? { settings: settingsPayload } : {}),
      });
      onEditSaveSuccess?.();
      onShowToast?.('User updated', 'success');
      await fetchUsers();
      return true;
    } catch {
      return fail('Failed to update user');
    } finally {
      setSaving(false);
    }
  };

  const deleteUser = async (userId: number) => {
    setDeletingUserId(userId);
    try {
      await deleteAdminUser(userId);
      onShowToast?.('User deleted', 'success');
      await fetchUsers();
      return true;
    } catch {
      return fail('Failed to delete user');
    } finally {
      setDeletingUserId(null);
    }
  };

  const syncCwaUsers = async () => {
    setSyncingCwa(true);
    try {
      const result = await syncAdminCwaUsers();
      onShowToast?.(result.message || 'Users synced from CWA', 'success');
      await fetchUsers();
      return true;
    } catch (err) {
      return fail(err instanceof Error ? err.message : 'Failed to sync users from CWA');
    } finally {
      setSyncingCwa(false);
    }
  };

  return {
    creating,
    saving,
    deletingUserId,
    syncingCwa,
    createUser,
    saveEditedUser,
    deleteUser,
    syncCwaUsers,
  };
};
