import { useState } from 'react';
import { AdminUser, DownloadDefaults } from '../../../services/api';
import {
  canCreateLocalUsersForAuthMode,
  CreateUserFormState,
} from './types';
import { UserAccountCardContent, UserCreateCard, UserIdentityHeader, UserRoleControl } from './UserCard';

interface UserListViewProps {
  authMode: string;
  users: AdminUser[];
  loadingUsers: boolean;
  loadError: string | null;
  onRetryLoadUsers: () => void;
  needsLocalAdmin: boolean;
  onCreate: () => void;
  showCreateForm: boolean;
  createForm: CreateUserFormState;
  onCreateFormChange: (form: CreateUserFormState) => void;
  creating: boolean;
  isFirstUser: boolean;
  onCreateSubmit: () => void;
  onCancelCreate: () => void;
  showEditForm: boolean;
  activeEditUserId: number | null;
  editingUser: AdminUser | null;
  onEditingUserChange: (user: AdminUser) => void;
  onEditSave: () => void;
  saving: boolean;
  onCancelEdit: () => void;
  editPassword: string;
  onEditPasswordChange: (value: string) => void;
  editPasswordConfirm: string;
  onEditPasswordConfirmChange: (value: string) => void;
  downloadDefaults: DownloadDefaults | null;
  onOpenOverrides: () => void;
  onEdit: (user: AdminUser) => void;
  onDelete: (userId: number) => Promise<boolean>;
  deletingUserId: number | null;
  onSyncCwa: () => Promise<void> | void;
  syncingCwa: boolean;
}

export const UserListView = ({
  authMode,
  users,
  loadingUsers,
  loadError,
  onRetryLoadUsers,
  needsLocalAdmin,
  onCreate,
  showCreateForm,
  createForm,
  onCreateFormChange,
  creating,
  isFirstUser,
  onCreateSubmit,
  onCancelCreate,
  showEditForm,
  activeEditUserId,
  editingUser,
  onEditingUserChange,
  onEditSave,
  saving,
  onCancelEdit,
  editPassword,
  onEditPasswordChange,
  editPasswordConfirm,
  onEditPasswordConfirmChange,
  downloadDefaults,
  onOpenOverrides,
  onEdit,
  onDelete,
  deletingUserId,
  onSyncCwa,
  syncingCwa,
}: UserListViewProps) => {
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const canCreateLocalUsers = canCreateLocalUsersForAuthMode(authMode);
  const isCwaMode = String(authMode || 'none').toLowerCase() === 'cwa';
  const handleDelete = async (userId: number) => {
    const ok = await onDelete(userId);
    if (ok) {
      setConfirmDelete(null);
    }
  };

  return (
    <div className="space-y-4">
      {(loadingUsers && users.length === 0) ? (
        <div className="text-center py-8 space-y-2">
          <p className="text-sm opacity-50">Loading users...</p>
        </div>
      ) : (loadError && users.length === 0) ? (
        <div className="text-center py-8 space-y-3">
          <p className="text-sm opacity-60">{loadError}</p>
          <button
            onClick={onRetryLoadUsers}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)]
                       bg-[var(--bg-soft)] hover:bg-[var(--hover-surface)] transition-colors"
          >
            Retry
          </button>
        </div>
      ) : users.length === 0 ? (
        <div className="text-center py-8 space-y-2">
          <p className="text-sm opacity-50">No users yet.</p>
          <p className="text-xs opacity-40">
            Create a local admin account before enabling OIDC to avoid getting locked out.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {users.map((user) => {
            const active = user.is_active !== false;
            const isEditingRow = showEditForm && activeEditUserId === user.id;
            const hasLoadedEditUser = isEditingRow && editingUser?.id === user.id;
            return (
              <div
                key={user.id}
                className={`rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] transition-colors ${active ? '' : 'opacity-60'}`}
              >
                <div
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    // Don't toggle when clicking interactive elements inside the header (e.g. role dropdown)
                    if ((e.target as HTMLElement).closest('button:not([data-card-toggle]), [role="listbox"], [data-dropdown]')) return;
                    setConfirmDelete(null);
                    if (isEditingRow) {
                      onCancelEdit();
                    } else {
                      onEdit(user);
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setConfirmDelete(null);
                      if (isEditingRow) {
                        onCancelEdit();
                      } else {
                        onEdit(user);
                      }
                    }
                  }}
                  className={`flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between p-3 cursor-pointer hover-surface rounded-t-lg ${isEditingRow ? 'border-b border-[var(--border-muted)]' : 'rounded-b-lg'}`}
                  aria-expanded={isEditingRow}
                  aria-label={isEditingRow ? 'Collapse user editor' : `Expand ${user.username} editor`}
                >
                  <UserIdentityHeader user={user} />

                  <div className="flex items-center flex-wrap gap-2 shrink-0 sm:justify-end">
                    {hasLoadedEditUser && editingUser ? (
                      <UserRoleControl
                        user={editingUser}
                        onUserChange={onEditingUserChange}
                        oidcAdminGroup={downloadDefaults?.OIDC_ADMIN_GROUP}
                        tooltipPosition="bottom"
                      />
                    ) : (
                      <UserRoleControl user={user} />
                    )}

                    <div
                      className="p-2 rounded-full text-gray-500 dark:text-gray-400"
                      aria-hidden="true"
                    >
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 24 24"
                        strokeWidth={1.5}
                        stroke="currentColor"
                        className={`w-[18px] h-[18px] transition-transform duration-200 ${isEditingRow ? 'rotate-180' : ''}`}
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="m19.5 8.25-7.5 7.5-7.5-7.5"
                        />
                      </svg>
                    </div>
                  </div>
                </div>

                {isEditingRow && (
                  <div className="p-4 space-y-5 bg-[var(--bg)] rounded-b-lg">
                    {hasLoadedEditUser && editingUser ? (
                      <UserAccountCardContent
                        user={editingUser}
                        onUserChange={onEditingUserChange}
                        onSave={onEditSave}
                        saving={saving}
                        onCancel={onCancelEdit}
                        editPassword={editPassword}
                        onEditPasswordChange={onEditPasswordChange}
                        editPasswordConfirm={editPasswordConfirm}
                        onEditPasswordConfirmChange={onEditPasswordConfirmChange}
                        onDelete={() => setConfirmDelete(user.id)}
                        onConfirmDelete={() => handleDelete(user.id)}
                        onCancelDelete={() => setConfirmDelete(null)}
                        isDeletePending={confirmDelete === user.id}
                        deleting={deletingUserId === user.id}
                        preferencesPanel={{
                          description: 'Customise delivery and request settings for this user.',
                          actionLabel: 'Open User Preferences',
                          onAction: onOpenOverrides,
                        }}
                      />
                    ) : (
                      <div className="text-sm opacity-60">Loading user details...</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {canCreateLocalUsers && (
        <div>
          {showCreateForm ? (
            <UserCreateCard
              form={createForm}
              onChange={onCreateFormChange}
              creating={creating}
              isFirstUser={isFirstUser}
              needsLocalAdmin={needsLocalAdmin}
              onSubmit={onCreateSubmit}
              onCancel={onCancelCreate}
            />
          ) : (
            <button
              onClick={onCreate}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors"
            >
              Create Local User
            </button>
          )}
        </div>
      )}

      {!canCreateLocalUsers && isCwaMode && (
        <div>
          <button
            onClick={onSyncCwa}
            disabled={syncingCwa}
            className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {syncingCwa ? 'Syncing with CWA...' : 'Sync with CWA'}
          </button>
        </div>
      )}
    </div>
  );
};
