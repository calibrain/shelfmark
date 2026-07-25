import { useMountEffect } from '../../../hooks/useMountEffect';
import type { AdminUser } from '../../../services/api';
import {
  canCreateLocalUsersForAuthMode,
  UserListView,
  useUserForm,
  useUserMutations,
  useUsersFetch,
  useUsersPanelState,
} from '../users';
import type { CustomSettingsFieldRendererProps } from './types';

export const UsersManagementField = ({
  authMode,
  onShowToast,
  onRefreshAuth,
}: CustomSettingsFieldRendererProps) => {
  const { route, openCreate, openEdit, backToList } = useUsersPanelState();
  const { users, loading, loadError, fetchUsers } = useUsersFetch({ onShowToast });
  const form = useUserForm();
  const mutations = useUserMutations({
    onShowToast,
    fetchUsers,
    createForm: form.createForm,
    resetCreateForm: form.resetCreateForm,
    editingUser: form.editingUser,
    editPassword: form.editPassword,
    editPasswordConfirm: form.editPasswordConfirm,
    onEditSaveSuccess: form.clearEditState,
  });
  useMountEffect(() => {
    void fetchUsers();
  });
  const startEditing = (user: AdminUser) => {
    form.beginEditing(user);
    openEdit(user.id);
  };
  const finishEdit = async () => {
    if (await mutations.saveEditedUser()) {
      backToList();
    }
  };
  const finishCreate = async () => {
    if (await mutations.createUser()) {
      void onRefreshAuth?.();
      backToList();
    }
  };
  return (
    <UserListView
      authMode={authMode || 'none'}
      users={users}
      loadingUsers={loading}
      loadError={loadError}
      onRetryLoadUsers={() => void fetchUsers({ force: true })}
      needsLocalAdmin={
        !users.some((user) => user.role === 'admin' && user.auth_source === 'builtin')
      }
      onCreate={openCreate}
      showCreateForm={route.kind === 'create' && canCreateLocalUsersForAuthMode(authMode || 'none')}
      createForm={form.createForm}
      onCreateFormChange={form.setCreateForm}
      creating={mutations.creating}
      isFirstUser={users.length === 0}
      onCreateSubmit={() => void finishCreate()}
      onCancelCreate={backToList}
      showEditForm={route.kind === 'edit'}
      activeEditUserId={route.kind === 'edit' ? route.userId : null}
      editingUser={form.editingUser}
      onEditingUserChange={form.setEditingUser}
      onEditSave={() => void finishEdit()}
      saving={mutations.saving}
      onCancelEdit={() => {
        form.clearEditState();
        backToList();
      }}
      editPassword={form.editPassword}
      onEditPasswordChange={form.setEditPassword}
      editPasswordConfirm={form.editPasswordConfirm}
      onEditPasswordConfirmChange={form.setEditPasswordConfirm}
      onEdit={startEditing}
      onDelete={mutations.deleteUser}
      deletingUserId={mutations.deletingUserId}
      onSyncCwa={mutations.syncCwaUsers}
      syncingCwa={mutations.syncingCwa}
    />
  );
};
