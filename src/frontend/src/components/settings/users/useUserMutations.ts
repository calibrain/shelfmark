import { useState } from 'react';

import type { AdminUser } from '../../../services/api';
import {
  createAdminUser,
  deleteAdminUser,
  syncAdminCwaUsers,
  updateAdminUser,
} from '../../../services/api';
import type { CreateUserFormState } from './types';

const MIN_PASSWORD_LENGTH = 4;

interface Params {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  fetchUsers: (options?: { force?: boolean }) => Promise<AdminUser[]>;
  createForm: CreateUserFormState;
  resetCreateForm: () => void;
  editingUser: AdminUser | null;
  editPassword: string;
  editPasswordConfirm: string;
  onEditSaveSuccess?: () => void;
}

export const useUserMutations = ({
  onShowToast,
  fetchUsers,
  createForm,
  resetCreateForm,
  editingUser,
  editPassword,
  editPasswordConfirm,
  onEditSaveSuccess,
}: Params) => {
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null);
  const [syncingCwa, setSyncingCwa] = useState(false);
  const fail = (message: string) => (onShowToast?.(message, 'error'), false);
  const createUser = async () => {
    if (!createForm.username || !createForm.password)
      return fail('Username and password are required');
    if (createForm.password.length < MIN_PASSWORD_LENGTH)
      return fail(`Password must be at least ${MIN_PASSWORD_LENGTH} characters`);
    if (createForm.password !== createForm.password_confirm) return fail('Passwords do not match');
    setCreating(true);
    try {
      await createAdminUser({
        username: createForm.username,
        password: createForm.password,
        email: createForm.email || undefined,
        display_name: createForm.display_name || undefined,
        role: createForm.role || undefined,
      });
      resetCreateForm();
      await fetchUsers({ force: true });
      onShowToast?.('Local user created', 'success');
      return true;
    } catch (error) {
      return fail(error instanceof Error ? error.message : 'Failed to create user');
    } finally {
      setCreating(false);
    }
  };
  const saveEditedUser = async () => {
    if (!editingUser) return false;
    if (editPassword && editPassword.length < MIN_PASSWORD_LENGTH)
      return fail(`Password must be at least ${MIN_PASSWORD_LENGTH} characters`);
    if (editPassword !== editPasswordConfirm) return fail('Passwords do not match');
    setSaving(true);
    try {
      await updateAdminUser(editingUser.id, {
        username: editingUser.username,
        role: editingUser.role,
        email: editingUser.email,
        display_name: editingUser.display_name,
        is_active: editingUser.is_active,
        library_capability: editingUser.library_capability,
        ...(editPassword ? { password: editPassword } : {}),
      });
      onEditSaveSuccess?.();
      await fetchUsers({ force: true });
      onShowToast?.('User updated', 'success');
      return true;
    } catch (error) {
      return fail(error instanceof Error ? error.message : 'Failed to update user');
    } finally {
      setSaving(false);
    }
  };
  const deleteUser = async (userId: number) => {
    setDeletingUserId(userId);
    try {
      await deleteAdminUser(userId);
      await fetchUsers({ force: true });
      onShowToast?.('User deleted', 'success');
      return true;
    } catch (error) {
      return fail(error instanceof Error ? error.message : 'Failed to delete user');
    } finally {
      setDeletingUserId(null);
    }
  };
  const syncCwaUsers = async () => {
    setSyncingCwa(true);
    try {
      const result = await syncAdminCwaUsers();
      await fetchUsers({ force: true });
      onShowToast?.(result.message || 'Users synced', 'success');
    } catch (error) {
      onShowToast?.(error instanceof Error ? error.message : 'Failed to sync users', 'error');
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
