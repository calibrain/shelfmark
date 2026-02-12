import { useEffect } from 'react';
import { AdminUser } from '../../services/api';
import { ActionResult, SettingsTab } from '../../types/settings';
import {
  canCreateLocalUsersForAuthMode,
  UserListView,
  UserOverridesView,
  useUserForm,
  useUserMutations,
  useUsersFetch,
  useUsersPanelState,
} from './users';
import { SettingsContent } from './SettingsContent';
import { SettingsSubpage } from './shared';

interface UsersPanelProps {
  authMode: string;
  tab: SettingsTab;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  onSave: () => Promise<void>;
  onAction: (key: string) => Promise<ActionResult>;
  isSaving: boolean;
  hasChanges: boolean;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSubpageStateChange?: (state: { title: string; onBack: () => void } | null) => void;
}

export const UsersPanel = ({
  authMode,
  tab,
  values,
  onChange,
  onSave,
  onAction,
  isSaving,
  hasChanges,
  onShowToast,
  onSubpageStateChange,
}: UsersPanelProps) => {
  const { route, openCreate, openEdit, openEditOverrides, backToList } = useUsersPanelState();

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
    isUserOverridable,
    userSettings,
    setUserSettings,
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
    createForm,
    resetCreateForm,
    editingUser,
    editPassword,
    editPasswordConfirm,
    userSettings,
    userOverridableSettings,
    deliveryPreferences,
    onEditSaveSuccess: clearEditState,
  });

  const startEditing = async (user: AdminUser) => {
    beginEditing(user);
    try {
      const context = await fetchUserEditContext(user.id);
      applyUserEditContext(context);
    } catch {
      resetEditContext();
    }
  };

  const canCreateLocalUsers = canCreateLocalUsersForAuthMode(authMode);

  const handleBackToList = () => {
    clearEditState();
    backToList();
  };

  const handleCancelCreate = () => {
    resetCreateForm();
    backToList();
  };

  const handleCreate = async () => {
    const ok = await createUser();
    if (ok) {
      backToList();
    }
  };

  const handleOpenOverrides = () => {
    if (editingUser) {
      openEditOverrides(editingUser.id);
    }
  };

  const handleEdit = async (user: AdminUser) => {
    openEdit(user.id);
    await startEditing(user);
  };

  const handleSyncCwa = async () => {
    await syncCwaUsers();
  };

  const handleBackToEdit = () => {
    if (editingUser) {
      openEdit(editingUser.id);
      return;
    }
    backToList();
  };

  useEffect(() => {
    if (!onSubpageStateChange) {
      return undefined;
    }

    if (route.kind === 'edit-overrides') {
      const username = editingUser && editingUser.id === route.userId
        ? editingUser.username
        : 'User';
      onSubpageStateChange({
        title: `Users / User Preferences: ${username}`,
        onBack: handleBackToEdit,
      });
    } else {
      onSubpageStateChange(null);
    }

    return () => {
      onSubpageStateChange(null);
    };
  }, [editingUser, handleBackToEdit, onSubpageStateChange, route]);

  useEffect(() => {
    if (route.kind === 'create' && !canCreateLocalUsers) {
      backToList();
    }
  }, [backToList, canCreateLocalUsers, route.kind]);

  const handleSave = async () => {
    const ok = await saveEditedUser();
    if (ok) {
      backToList();
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm opacity-60 p-8">
        Loading users...
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 gap-3">
        <p className="text-sm opacity-60">{loadError}</p>
        <button
          onClick={fetchUsers}
          className="px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)]
                     bg-[var(--bg-soft)] hover:bg-[var(--hover-surface)] transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  if (route.kind === 'edit-overrides') {
    if (!editingUser || editingUser.id !== route.userId) {
      return (
        <div className="flex-1 flex items-center justify-center text-sm opacity-60 p-8">
          Loading user details...
        </div>
      );
    }

    return (
      <UserOverridesView
        onSave={handleSave}
        saving={saving}
        onBack={handleBackToEdit}
        deliveryPreferences={deliveryPreferences}
        isUserOverridable={isUserOverridable}
        userSettings={userSettings}
        setUserSettings={(updater) => setUserSettings(updater)}
      />
    );
  }

  return (
    <SettingsSubpage>
      <div>
        <UserListView
          authMode={authMode}
          users={users}
          onCreate={openCreate}
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
          onEditSave={handleSave}
          saving={saving}
          onCancelEdit={handleBackToList}
          editPassword={editPassword}
          onEditPasswordChange={setEditPassword}
          editPasswordConfirm={editPasswordConfirm}
          onEditPasswordConfirmChange={setEditPasswordConfirm}
          downloadDefaults={downloadDefaults}
          onOpenOverrides={handleOpenOverrides}
          onEdit={handleEdit}
          onDelete={deleteUser}
          deletingUserId={deletingUserId}
          onSyncCwa={handleSyncCwa}
          syncingCwa={syncingCwa}
        />

        <div className="pt-5 mt-4 border-t border-black/10 dark:border-white/10">
          <SettingsContent
            tab={tab}
            values={values}
            onChange={onChange}
            onSave={onSave}
            onAction={onAction}
            isSaving={isSaving}
            hasChanges={hasChanges}
            embedded
          />
        </div>
      </div>
    </SettingsSubpage>
  );
};
