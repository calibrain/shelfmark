import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';
import {
  AdminUser,
  testAdminUserNotificationPreferences,
} from '../../../services/api';
import { CustomSettingsFieldRendererProps } from './types';
import {
  canCreateLocalUsersForAuthMode,
  UserListView,
  UserOverridesView,
  useUserForm,
  useUserMutations,
  useUsersFetch,
  useUsersPanelState,
} from '../users';

export const UsersManagementField = ({
  tab: usersTab,
  values,
  onUiStateChange,
  authMode,
  onShowToast,
  onRefreshOverrideSummary,
  onRefreshAuth,
}: CustomSettingsFieldRendererProps) => {
  const { route, openCreate, openEdit, openEditOverrides, backToList } = useUsersPanelState();
  const activeEditRequestIdRef = useRef(0);

  const {
    users,
    loading,
    loadError,
    fetchUsers,
    fetchUserEditContext,
  } = useUsersFetch({ onShowToast });

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
  const needsLocalAdmin = !users.some(u => u.role === 'admin' && u.auth_source === 'builtin');

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
    const ok = await createUser();
    if (ok) {
      onRefreshOverrideSummary?.();
      onRefreshAuth?.();
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

  useEffect(() => {
    if (route.kind === 'create' && !canCreateLocalUsers) {
      backToList();
    }
  }, [backToList, canCreateLocalUsers, route.kind]);

  useLayoutEffect(() => {
    onUiStateChange('routeKind', route.kind);
  }, [onUiStateChange, route.kind]);

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
      onRefreshOverrideSummary?.();
      backToList();
    }
  }, [backToList, onRefreshOverrideSummary, saveEditedUser]);

  const handleSaveUserOverridesRef = useRef(handleSaveUserOverrides);
  useEffect(() => {
    handleSaveUserOverridesRef.current = handleSaveUserOverrides;
  }, [handleSaveUserOverrides]);

  const triggerSaveUserOverrides = useCallback(async () => {
    await handleSaveUserOverridesRef.current();
  }, []);

  const handleTestNotificationRoutes = useCallback(async (routes: Array<Record<string, unknown>>) => {
    if (!editingUser) {
      return { success: false, message: 'No user selected for notification test.' };
    }
    return testAdminUserNotificationPreferences(editingUser.id, routes);
  }, [editingUser]);

  const handleDeleteUser = useCallback(async (userId: number) => {
    const ok = await deleteUser(userId);
    if (ok) {
      onRefreshOverrideSummary?.();
      onRefreshAuth?.();
    }
    return ok;
  }, [deleteUser, onRefreshAuth, onRefreshOverrideSummary]);

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
        <div className="flex items-center justify-center text-sm opacity-60 py-8">
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
        if (needsLocalAdmin) {
          setCreateForm({ ...createForm, role: 'admin' });
        }
        openCreate();
      }}
      needsLocalAdmin={needsLocalAdmin}
      showCreateForm={route.kind === 'create'}
      createForm={createForm}
      onCreateFormChange={setCreateForm}
      creating={creating}
      isFirstUser={users.length === 0}
      onCreateSubmit={handleCreate}
      onCancelCreate={handleCancelCreate}
      showEditForm={route.kind === 'edit'}
      activeEditUserId={route.kind === 'edit' ? route.userId : null}
      editingUser={route.kind === 'edit' ? editingUser : null}
      onEditingUserChange={setEditingUser}
      onEditSave={handleSaveUserEdit}
      saving={saving}
      onCancelEdit={handleBackToList}
      editPassword={editPassword}
      onEditPasswordChange={setEditPassword}
      editPasswordConfirm={editPasswordConfirm}
      onEditPasswordConfirmChange={setEditPasswordConfirm}
      downloadDefaults={downloadDefaults}
      onOpenOverrides={handleOpenOverrides}
      onEdit={handleEdit}
      onDelete={handleDeleteUser}
      deletingUserId={deletingUserId}
      onSyncCwa={handleSyncCwa}
      syncingCwa={syncingCwa}
    />
  );
};
