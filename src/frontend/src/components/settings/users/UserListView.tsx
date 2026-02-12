import { useState } from 'react';
import { AdminUser, DownloadDefaults } from '../../../services/api';
import {
  canCreateLocalUsersForAuthMode,
  CreateUserFormState,
  getUsersHeadingDescriptionForAuthMode,
} from './types';
import { UserAuthSourceBadge } from './UserAuthSourceBadge';
import { UserCreateCard, UserEditFields } from './UserCard';
import { HeadingField } from '../fields';
import { HeadingFieldConfig } from '../../../types/settings';

interface UserListViewProps {
  authMode: string;
  users: AdminUser[];
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
  const usersHeading: HeadingFieldConfig = {
    key: 'users_heading',
    type: 'HeadingField',
    title: 'Users',
    description: getUsersHeadingDescriptionForAuthMode(authMode),
  };

  const handleDelete = async (userId: number) => {
    const ok = await onDelete(userId);
    if (ok) {
      setConfirmDelete(null);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <HeadingField field={usersHeading} />
      </div>

      {users.length === 0 ? (
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
            const roleLabel = user.role.charAt(0).toUpperCase() + user.role.slice(1);
            return (
              <div
                key={user.id}
                className={`rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] transition-colors ${active ? '' : 'opacity-60'}`}
              >
                <div className={`flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between p-3 ${isEditingRow ? 'border-b border-[var(--border-muted)]' : ''}`}>
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div
                      className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium shrink-0
                        ${user.role === 'admin' ? 'bg-sky-500/20 text-sky-600 dark:text-sky-400' : 'bg-zinc-500/20'}`}
                    >
                      {user.username.charAt(0).toUpperCase()}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium truncate">
                          {user.display_name || user.username}
                        </span>
                        {user.display_name && (
                          <span className="text-xs opacity-40 truncate">@{user.username}</span>
                        )}
                        <UserAuthSourceBadge user={user} />
                      </div>
                      <div className="text-xs opacity-50 truncate">
                        {user.email || 'No email'}
                      </div>
                      {!active && (
                        <div className="text-[11px] opacity-60 truncate">
                          Inactive for current authentication mode
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center flex-wrap gap-2 shrink-0 sm:justify-end">
                    <span
                      className={`inline-flex items-center rounded-md px-2.5 py-1 text-xs font-medium leading-none
                        ${user.role === 'admin' ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400' : 'bg-zinc-500/10 opacity-70'}`}
                    >
                      {roleLabel}
                    </span>

                    {!isEditingRow && (
                      <>
                        <button
                          onClick={() => onEdit(user)}
                          className="p-2 rounded-full hover-action transition-colors text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
                          aria-label={`Edit ${user.username}`}
                          title="Edit user"
                        >
                          <svg
                            xmlns="http://www.w3.org/2000/svg"
                            fill="none"
                            viewBox="0 0 24 24"
                            strokeWidth={1.5}
                            stroke="currentColor"
                            className="w-[18px] h-[18px]"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"
                            />
                          </svg>
                        </button>

                        {confirmDelete === user.id ? (
                          <div className="flex items-center gap-1">
                            <button
                              onClick={() => handleDelete(user.id)}
                              disabled={deletingUserId === user.id}
                              className="text-xs font-medium px-2.5 py-1.5 rounded-lg bg-red-600 text-white hover:bg-red-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                            >
                              {deletingUserId === user.id ? 'Deleting...' : 'Confirm'}
                            </button>
                            <button
                              onClick={() => setConfirmDelete(null)}
                              className="text-xs font-medium px-2.5 py-1.5 rounded-lg border border-[var(--border-muted)]
                                         bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => setConfirmDelete(user.id)}
                            className="p-2 rounded-full hover-action transition-colors text-red-500 hover:text-red-600 dark:text-red-400 dark:hover:text-red-300"
                            aria-label={`Delete ${user.username}`}
                            title="Delete user"
                          >
                            <svg
                              xmlns="http://www.w3.org/2000/svg"
                              fill="none"
                              viewBox="0 0 24 24"
                              strokeWidth={1.5}
                              stroke="currentColor"
                              className="w-[18px] h-[18px]"
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
                              />
                            </svg>
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>

                {isEditingRow && (
                  <div className="p-4 space-y-5 bg-[var(--bg)] rounded-b-lg">
                    {hasLoadedEditUser && editingUser ? (
                      <UserEditFields
                        user={editingUser}
                        onUserChange={onEditingUserChange}
                        onSave={onEditSave}
                        saving={saving}
                        onCancel={onCancelEdit}
                        editPassword={editPassword}
                        onEditPasswordChange={onEditPasswordChange}
                        editPasswordConfirm={editPasswordConfirm}
                        onEditPasswordConfirmChange={onEditPasswordConfirmChange}
                        downloadDefaults={downloadDefaults}
                        onEditOverrides={onOpenOverrides}
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
