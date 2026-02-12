import { useState } from 'react';
import { AdminUser, DeliveryPreferencesResponse, DownloadDefaults } from '../../../services/api';
import { CreateUserFormState, INITIAL_CREATE_FORM, PerUserSettings } from './types';
import { UserEditContext } from './useUsersFetch';

export const useUserForm = () => {
  const [createForm, setCreateForm] = useState<CreateUserFormState>({ ...INITIAL_CREATE_FORM });
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [editPassword, setEditPassword] = useState('');
  const [editPasswordConfirm, setEditPasswordConfirm] = useState('');
  const [downloadDefaults, setDownloadDefaults] = useState<DownloadDefaults | null>(null);
  const [deliveryPreferences, setDeliveryPreferences] = useState<DeliveryPreferencesResponse | null>(null);
  const [userSettings, setUserSettings] = useState<PerUserSettings>({});
  const [userOverridableSettings, setUserOverridableSettings] = useState<Set<string>>(new Set());

  const resetCreateForm = () => setCreateForm({ ...INITIAL_CREATE_FORM });

  const resetEditContext = () => {
    setDownloadDefaults(null);
    setDeliveryPreferences(null);
    setUserSettings({});
    setUserOverridableSettings(new Set());
  };

  const beginEditing = (user: AdminUser) => {
    setEditingUser({ ...user });
    setEditPassword('');
    setEditPasswordConfirm('');
  };

  const applyUserEditContext = (context: UserEditContext) => {
    setEditingUser({ ...context.user });
    setDownloadDefaults(context.downloadDefaults);
    setDeliveryPreferences(context.deliveryPreferences);
    setUserSettings(context.userSettings);
    setUserOverridableSettings(new Set(context.userOverridableSettings));
  };

  const clearEditState = () => {
    setEditingUser(null);
    setEditPassword('');
    setEditPasswordConfirm('');
    resetEditContext();
  };

  const isUserOverridable = (key: keyof PerUserSettings) => userOverridableSettings.has(String(key));

  return {
    createForm,
    setCreateForm,
    resetCreateForm,
    editingUser,
    setEditingUser,
    beginEditing,
    applyUserEditContext,
    resetEditContext,
    clearEditState,
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
