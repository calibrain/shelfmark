import { useMemo, useState } from 'react';

import type {
  AdminUser,
  DeliveryPreferencesResponse,
  DownloadDefaults,
} from '../../../services/api';
import { INITIAL_CREATE_FORM } from './types';
import { useUserOverridesState } from './useUserOverridesState';
import type { UserEditContext } from './useUsersFetch';

export const useUserForm = () => {
  const [createForm, setCreateForm] = useState({ ...INITIAL_CREATE_FORM });
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [editPassword, setEditPassword] = useState('');
  const [editPasswordConfirm, setEditPasswordConfirm] = useState('');
  const [downloadDefaults, setDownloadDefaults] = useState<DownloadDefaults | null>(null);
  const [deliveryPreferences, setDeliveryPreferences] =
    useState<DeliveryPreferencesResponse | null>(null);
  const [searchPreferences, setSearchPreferences] = useState<DeliveryPreferencesResponse | null>(
    null,
  );
  const [notificationPreferences, setNotificationPreferences] =
    useState<DeliveryPreferencesResponse | null>(null);
  const preferenceGroups = useMemo(
    () => [deliveryPreferences, searchPreferences, notificationPreferences],
    [deliveryPreferences, searchPreferences, notificationPreferences],
  );
  const {
    userSettings,
    setUserSettings,
    userOverridableSettings,
    isUserOverridable,
    hasUserSettingsChanges,
    hasUserSettingsChangesFor,
    applyUserOverridesContext,
    resetUserOverridesState,
  } = useUserOverridesState({ preferenceGroups });

  const resetCreateForm = () => setCreateForm({ ...INITIAL_CREATE_FORM });

  const resetEditContext = () => {
    setDownloadDefaults(null);
    setDeliveryPreferences(null);
    setSearchPreferences(null);
    setNotificationPreferences(null);
    resetUserOverridesState();
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
    setSearchPreferences(context.searchPreferences);
    setNotificationPreferences(context.notificationPreferences);
    applyUserOverridesContext({
      settings: context.userSettings,
      userOverridableKeys: context.userOverridableSettings,
    });
  };

  const clearEditState = () => {
    setEditingUser(null);
    setEditPassword('');
    setEditPasswordConfirm('');
    resetEditContext();
  };

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
    searchPreferences,
    notificationPreferences,
    userSettings,
    setUserSettings,
    hasUserSettingsChanges,
    hasUserSettingsChangesFor,
    userOverridableSettings,
    isUserOverridable,
  };
};
