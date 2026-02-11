import { useCallback, useEffect } from 'react';
import { AdminUser } from '../../services/api';
import {
  UserListView,
  UserOverridesView,
  useUsersData,
  useUsersPanelState,
} from './users';

interface UsersPanelProps {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSubpageStateChange?: (state: { title: string; onBack: () => void } | null) => void;
}

export const UsersPanel = ({ onShowToast, onSubpageStateChange }: UsersPanelProps) => {
  const { route, openCreate, openEdit, openEditOverrides, backToList } = useUsersPanelState();
  const {
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
    isUserOverridable,
    userSettings,
    setUserSettings,
    overrides,
    toggleOverride,
    bookloreLibraries,
    booklorePaths,
  } = useUsersData({ onShowToast });

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

  const handleBackToEdit = useCallback(() => {
    if (editingUser) {
      openEdit(editingUser.id);
      return;
    }
    backToList();
  }, [backToList, editingUser, openEdit]);

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
        user={editingUser}
        onSave={handleSave}
        saving={saving}
        onBack={handleBackToEdit}
        downloadDefaults={downloadDefaults}
        isUserOverridable={isUserOverridable}
        userSettings={userSettings}
        setUserSettings={(updater) => setUserSettings(updater)}
        overrides={overrides}
        toggleOverride={toggleOverride}
        bookloreLibraries={bookloreLibraries}
        booklorePaths={booklorePaths}
      />
    );
  }

  return (
    <UserListView
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
    />
  );
};
