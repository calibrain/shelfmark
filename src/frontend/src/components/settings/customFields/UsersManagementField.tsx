import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';

import type { AdminUser } from '../../../services/api';
import { testAdminUserNotificationPreferences } from '../../../services/api';
import {
  canCreateLocalUsersForAuthMode,
  UserListView,
  UserOverridesView,
  useUserForm,
  useUserMutations,
  useUsersFetch,
  useUsersPanelState,
} from '../users';
import type { CustomSettingsFieldRendererProps } from './types';

export const UsersManagementField = ({
  tab: usersTab,
  values,
  onUiStateChange,
  authMode,
  onShowToast,
  onRefreshOverrideSummary,
  onRefreshAuth,
  onSettingsSaved,
}: CustomSettingsFieldRendererProps) => {
  const { route, openCreate, openEdit, openEditOverrides, backToList } = useUsersPanelState();
  const activeEditRequestIdRef = useRef(0);

  const { users, loading, loadError, fetchUsers, fetchUserEditContext } = useUsersFetch({
    onShowToast,
  });

  const {
    createForm,
    setCreateForm,
    resetCreateForm,
    editingUser,
    setEditingUser,
    editPassword,
    setEditPassword,
    editPasswordConfirm,
    setEditPasswordConfirm,
    downloadDefaults,
    deliveryPreferences,
    searchPreferences,
    notificationPreferences,
    isUserOverridable,
    userSettings,
    setUserSettings,
    hasUserSettingsChanges,
    beginEditing,
    applyUserEditContext,
    resetEditContext,
    clearEditState,
    userOverridableSettings,
  } = useUserForm();

  const {
    creating,
    saving,
    deletingUserId,
    syncingCwa,
    createUser,
    saveEditedUser,
    deleteUser,
    syncCwaUsers,
  } = useUserMutations({
    onShowToast,
    fetchUsers,
    users,
    createForm,
    resetCreateForm,
    editingUser,
    editPassword,
    editPasswordConfirm,
    userSettings,
    userOverridableSettings,
    deliveryPreferences,
    searchPreferences,
    notificationPreferences,
    onEditSaveSuccess: clearEditState,
  });

  const invalidateEditContextRequest = useCallback(() => {
    activeEditRequestIdRef.current += 1;
  }, []);

  useEffect(() => {
    return () => {
      invalidateEditContextRequest();
    };
  }, [invalidateEditContextRequest]);

  const startEditing = async (user: AdminUser) => {
    const requestId = activeEditRequestIdRef.current + 1;
    activeEditRequestIdRef.current = requestId;
    beginEditing(user);
    try {
      const context = await fetchUserEditContext(user.id);
      if (activeEditRequestIdRef.current !== requestId) {
        return;
      }
      applyUserEditContext(context);
    } catch {
      if (activeEditRequestIdRef.current !== requestId) {
        return;
      }
      resetEditContext();
    }
  };

  const canCreateLocalUsers = canCreateLocalUsersForAuthMode(authMode || 'none');
  const effectiveRouteKind = route.kind === 'create' && !canCreateLocalUsers ? 'list' : route.kind;
  const activeEditUserId = route.kind === 'edit' ? route.userId : null;
  const needsLocalAdmin = !users.some((u) => u.role === 'admin' && u.auth_source === 'builtin');

  const handleBackToList = () => {
    onUiStateChange('routeKind', 'list');
    invalidateEditContextRequest();
    clearEditState();
    backToList();
  };

  const handleCancelCreate = () => {
    onUiStateChange('routeKind', 'list');
    resetCreateForm();
    backToList();
  };

  const handleCreate = async () => {
    if (!canCreateLocalUsers) {
      return;
    }
    const ok = await createUser();
    if (ok) {
      onRefreshOverrideSummary?.();
      void onRefreshAuth?.();
      backToList();
    }
  };

  const handleOpenOverrides = () => {
    if (editingUser) {
      onUiStateChange('routeKind', 'edit-overrides');
      openEditOverrides(editingUser.id);
    }
  };

  const handleEdit = async (user: AdminUser) => {
    onUiStateChange('routeKind', 'edit');
    openEdit(user.id);
    await startEditing(user);
  };

  const handleSyncCwa = async () => {
    const ok = await syncCwaUsers();
    if (ok) {
      onRefreshOverrideSummary?.();
    }
  };

  const handleBackToEdit = () => {
    if (editingUser) {
      onUiStateChange('routeKind', 'edit');
      openEdit(editingUser.id);
      return;
    }
    onUiStateChange('routeKind', 'list');
    backToList();
  };

  useLayoutEffect(() => {
    onUiStateChange('routeKind', effectiveRouteKind);
  }, [effectiveRouteKind, onUiStateChange]);

  const handleSaveUserEdit = useCallback(async () => {
    const ok = await saveEditedUser({ includeSettings: false });
    if (ok) {
      onRefreshOverrideSummary?.();
      backToList();
    }
  }, [backToList, onRefreshOverrideSummary, saveEditedUser]);

  const handleSaveUserOverrides = useCallback(async () => {
    const ok = await saveEditedUser({
      includeProfile: false,
      includePassword: false,
      includeSettings: true,
    });
    if (ok) {
      onSettingsSaved?.();
      onRefreshOverrideSummary?.();
      backToList();
    }
  }, [backToList, onRefreshOverrideSummary, onSettingsSaved, saveEditedUser]);

  const handleSaveUserOverridesRef = useRef(handleSaveUserOverrides);
  useEffect(() => {
    handleSaveUserOverridesRef.current = handleSaveUserOverrides;
  }, [handleSaveUserOverrides]);

  const triggerSaveUserOverrides = useCallback(async () => {
    await handleSaveUserOverridesRef.current();
  }, []);

  const handleTestNotificationRoutes = useCallback(
    async (routes: Array<Record<string, unknown>>) => {
      if (!editingUser) {
        return { success: false, message: 'No user selected for notification test.' };
      }
      return testAdminUserNotificationPreferences(editingUser.id, routes);
    },
    [editingUser],
  );

  const handleDeleteUser = useCallback(
    async (userId: number) => {
      const ok = await deleteUser(userId);
      if (ok) {
        onRefreshOverrideSummary?.();
        void onRefreshAuth?.();
      }
      return ok;
    },
    [deleteUser, onRefreshAuth, onRefreshOverrideSummary],
  );

  useEffect(() => {
    if (route.kind !== 'edit-overrides') {
      onUiStateChange('hasChanges', false);
      onUiStateChange('isSaving', false);
      onUiStateChange('onSave', undefined);
      return;
    }

    onUiStateChange('hasChanges', hasUserSettingsChanges);
    onUiStateChange('isSaving', saving);
    onUiStateChange('onSave', triggerSaveUserOverrides);
  }, [hasUserSettingsChanges, onUiStateChange, route.kind, saving, triggerSaveUserOverrides]);

  if (route.kind === 'edit-overrides') {
    if (!editingUser || editingUser.id !== route.userId) {
      return (
        <div className="flex items-center justify-center py-8 text-sm opacity-60">
          Loading user details...
        </div>
      );
    }

    return (
      <UserOverridesView
        embedded
        hasChanges={hasUserSettingsChanges}
        onBack={handleBackToEdit}
        deliveryPreferences={deliveryPreferences}
        searchPreferences={searchPreferences}
        notificationPreferences={notificationPreferences}
        isUserOverridable={isUserOverridable}
        userSettings={userSettings}
        setUserSettings={(updater) => setUserSettings(updater)}
        usersTab={usersTab}
        globalUsersSettingsValues={values}
        onTestNotificationRoutes={handleTestNotificationRoutes}
      />
    );
  }

  return (
    <UserListView
      authMode={authMode || 'none'}
      users={users}
      loadingUsers={loading}
      loadError={loadError}
      onRetryLoadUsers={() => void fetchUsers({ force: true })}
      onCreate={() => {
        if (!canCreateLocalUsers) {
          return;
        }
        if (needsLocalAdmin) {
          setCreateForm({ ...createForm, role: 'admin' });
        }
        openCreate();
      }}
      needsLocalAdmin={needsLocalAdmin}
      showCreateForm={effectiveRouteKind === 'create'}
      createForm={createForm}
      onCreateFormChange={setCreateForm}
      creating={creating}
      isFirstUser={users.length === 0}
      onCreateSubmit={() => {
        void handleCreate();
      }}
      onCancelCreate={handleCancelCreate}
      showEditForm={effectiveRouteKind === 'edit'}
      activeEditUserId={activeEditUserId}
      editingUser={effectiveRouteKind === 'edit' ? editingUser : null}
      onEditingUserChange={setEditingUser}
      onEditSave={() => {
        void handleSaveUserEdit();
      }}
      saving={saving}
      onCancelEdit={handleBackToList}
      editPassword={editPassword}
      onEditPasswordChange={setEditPassword}
      editPasswordConfirm={editPasswordConfirm}
      onEditPasswordConfirmChange={setEditPasswordConfirm}
      downloadDefaults={downloadDefaults}
      onOpenOverrides={handleOpenOverrides}
      onEdit={(user) => {
        void handleEdit(user);
      }}
      onDelete={handleDeleteUser}
      deletingUserId={deletingUserId}
      onSyncCwa={handleSyncCwa}
      syncingCwa={syncingCwa}
    />
  );
};
