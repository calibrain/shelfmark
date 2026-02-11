import { ReactNode } from 'react';
import { AdminUser, DownloadDefaults } from '../../../services/api';
import { PasswordFieldConfig, SelectFieldConfig, TextFieldConfig } from '../../../types/settings';
import { PasswordField, SelectField, TextField } from '../fields';
import { FieldWrapper } from '../shared';
import { CreateUserFormState, getUserEditCapabilities } from './types';

interface UserCardShellProps {
  title: string;
  children: ReactNode;
}

const UserCardShell = ({ title, children }: UserCardShellProps) => (
  <div className="space-y-5 p-4 rounded-lg border border-[var(--border-muted)] bg-[var(--bg)]">
    <h3 className="text-sm font-medium">{title}</h3>
    {children}
  </div>
);

interface UserCreateCardProps {
  form: CreateUserFormState;
  onChange: (form: CreateUserFormState) => void;
  creating: boolean;
  isFirstUser: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}

export const UserCreateCard = ({
  form,
  onChange,
  creating,
  isFirstUser,
  onSubmit,
  onCancel,
}: UserCreateCardProps) => {
  const usernameField: TextFieldConfig = {
    type: 'TextField',
    key: 'username',
    label: 'Username',
    value: form.username,
    placeholder: 'username',
    required: true,
  };

  const displayNameField: TextFieldConfig = {
    type: 'TextField',
    key: 'display_name',
    label: 'Display Name',
    value: form.display_name,
    placeholder: 'Display name',
  };

  const emailField: TextFieldConfig = {
    type: 'TextField',
    key: 'email',
    label: 'Email',
    value: form.email,
    placeholder: 'user@example.com',
  };

  const passwordField: PasswordFieldConfig = {
    type: 'PasswordField',
    key: 'password',
    label: 'Password',
    value: form.password,
    placeholder: 'Min 4 characters',
    required: true,
  };

  const roleField: SelectFieldConfig = {
    type: 'SelectField',
    key: 'role',
    label: 'Role',
    value: form.role,
    options: [
      { value: 'user', label: 'User' },
      { value: 'admin', label: 'Admin' },
    ],
  };

  return (
    <UserCardShell title="Create User">
      {isFirstUser && (
        <div className="text-xs px-3 py-2 rounded-lg bg-sky-500/10 text-sky-400">
          This will be the first account and will be created as admin.
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <FieldWrapper field={usernameField}>
          <TextField
            field={usernameField}
            value={form.username}
            onChange={(value) => onChange({ ...form, username: value })}
          />
        </FieldWrapper>

        <FieldWrapper field={displayNameField}>
          <TextField
            field={displayNameField}
            value={form.display_name}
            onChange={(value) => onChange({ ...form, display_name: value })}
          />
        </FieldWrapper>

        <FieldWrapper field={emailField}>
          <TextField
            field={emailField}
            value={form.email}
            onChange={(value) => onChange({ ...form, email: value })}
          />
        </FieldWrapper>

        <FieldWrapper field={passwordField}>
          <PasswordField
            field={passwordField}
            value={form.password}
            onChange={(value) => onChange({ ...form, password: value })}
          />
        </FieldWrapper>
      </div>

      <FieldWrapper field={roleField}>
        <SelectField
          field={roleField}
          value={form.role}
          onChange={(value) => onChange({ ...form, role: value })}
        />
      </FieldWrapper>

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={onSubmit}
          disabled={creating}
          className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {creating ? 'Creating...' : 'Create User'}
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)]
                     bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors"
        >
          Cancel
        </button>
      </div>
    </UserCardShell>
  );
};

interface UserEditFieldsProps {
  user: AdminUser;
  onUserChange: (user: AdminUser) => void;
  onSave: () => void;
  saving: boolean;
  onCancel: () => void;
  editPassword: string;
  onEditPasswordChange: (value: string) => void;
  editPasswordConfirm: string;
  onEditPasswordConfirmChange: (value: string) => void;
  downloadDefaults: DownloadDefaults | null;
  onEditOverrides?: () => void;
}

export const UserEditFields = ({
  user,
  onUserChange,
  onSave,
  saving,
  onCancel,
  editPassword,
  onEditPasswordChange,
  editPasswordConfirm,
  onEditPasswordConfirmChange,
  downloadDefaults,
  onEditOverrides,
}: UserEditFieldsProps) => {
  const capabilities = getUserEditCapabilities(user, downloadDefaults?.OIDC_USE_ADMIN_GROUP);
  const { authSource, canSetPassword, canEditRole, canEditEmail, canEditDisplayName } = capabilities;

  const displayNameField: TextFieldConfig = {
    type: 'TextField',
    key: 'display_name',
    label: 'Display Name',
    value: user.display_name || '',
    placeholder: 'Display name',
  };

  const emailField: TextFieldConfig = {
    type: 'TextField',
    key: 'email',
    label: 'Email',
    value: user.email || '',
    placeholder: 'user@example.com',
  };

  const roleField: SelectFieldConfig = {
    type: 'SelectField',
    key: 'role',
    label: 'Role',
    value: user.role,
    options: [
      { value: 'admin', label: 'Admin' },
      { value: 'user', label: 'User' },
    ],
  };

  const newPasswordField: PasswordFieldConfig = {
    type: 'PasswordField',
    key: 'new_password',
    label: 'New Password',
    value: editPassword,
    placeholder: 'Leave empty to keep current',
  };

  const confirmPasswordField: PasswordFieldConfig = {
    type: 'PasswordField',
    key: 'confirm_password',
    label: 'Confirm Password',
    value: editPasswordConfirm,
    placeholder: 'Confirm new password',
    required: true,
  };

  const displayNameDisabledReason = !canEditDisplayName
    ? 'Display name is managed by the identity provider.'
    : undefined;

  const emailDisabledReason = !canEditEmail
    ? (authSource === 'cwa'
      ? 'Email is synced from Calibre-Web.'
      : 'Email is managed by your identity provider.')
    : undefined;

  const roleDisabledReason = !canEditRole
    ? (authSource === 'oidc'
      ? (downloadDefaults?.OIDC_ADMIN_GROUP
        ? `Role is managed by the ${downloadDefaults.OIDC_ADMIN_GROUP} group in your identity provider.`
        : 'Role is managed by OIDC group authorization.')
      : 'Role is managed by the external authentication source.')
    : undefined;

  return (
    <>
      {authSource !== 'builtin' && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs bg-sky-500/10 text-sky-400">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a.75.75 0 000 1.5h.253a.25.25 0 01.244.304l-.459 2.066A1.75 1.75 0 0010.747 15H11a.75.75 0 000-1.5h-.253a.25.25 0 01-.244-.304l.459-2.066A1.75 1.75 0 009.253 9H9z" clipRule="evenodd" />
            </svg>
            {authSource === 'oidc' && 'This user authenticates via OIDC. Password, email, and display name are managed by the identity provider.'}
            {authSource === 'proxy' && 'This user authenticates via proxy headers. Password authentication is unavailable for proxy users.'}
            {authSource === 'cwa' && 'This user authenticates via Calibre-Web. Password authentication is unavailable in Shelfmark for CWA users.'}
          </div>
          {authSource === 'oidc' && downloadDefaults?.OIDC_USE_ADMIN_GROUP === true && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs bg-sky-500/10 text-sky-400">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
                <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a.75.75 0 000 1.5h.253a.25.25 0 01.244.304l-.459 2.066A1.75 1.75 0 0010.747 15H11a.75.75 0 000-1.5h-.253a.25.25 0 01-.244-.304l.459-2.066A1.75 1.75 0 009.253 9H9z" clipRule="evenodd" />
              </svg>
              {downloadDefaults?.OIDC_ADMIN_GROUP
                ? `Admin role is managed by the ${downloadDefaults.OIDC_ADMIN_GROUP} group in your identity provider.`
                : 'Admin group authorization is enabled but no group name is configured.'}
            </div>
          )}
        </div>
      )}

      <FieldWrapper
        field={displayNameField}
        disabledOverride={!canEditDisplayName}
        disabledReasonOverride={displayNameDisabledReason}
      >
        <TextField
          field={displayNameField}
          value={user.display_name || ''}
          onChange={(value) => onUserChange({ ...user, display_name: value || null })}
          disabled={!canEditDisplayName}
        />
      </FieldWrapper>

      <FieldWrapper
        field={emailField}
        disabledOverride={!canEditEmail}
        disabledReasonOverride={emailDisabledReason}
      >
        <TextField
          field={emailField}
          value={user.email || ''}
          onChange={(value) => onUserChange({ ...user, email: value || null })}
          disabled={!canEditEmail}
        />
      </FieldWrapper>

      <FieldWrapper
        field={roleField}
        disabledOverride={!canEditRole}
        disabledReasonOverride={roleDisabledReason}
      >
        <SelectField
          field={roleField}
          value={user.role}
          onChange={(value) => onUserChange({ ...user, role: value })}
          disabled={!canEditRole}
        />
      </FieldWrapper>

      {canSetPassword && (
        <>
          <div className="border-t border-[var(--border-muted)] pt-4">
            <p className="text-xs font-medium opacity-60 mb-3">Change Password</p>
          </div>
          <FieldWrapper field={newPasswordField}>
            <PasswordField
              field={newPasswordField}
              value={editPassword}
              onChange={onEditPasswordChange}
            />
          </FieldWrapper>
          {editPassword && (
            <FieldWrapper field={confirmPasswordField}>
              <PasswordField
                field={confirmPasswordField}
                value={editPasswordConfirm}
                onChange={onEditPasswordConfirmChange}
              />
            </FieldWrapper>
          )}
        </>
      )}

      <div className="flex flex-wrap gap-2 pt-2">
        <button
          onClick={onSave}
          disabled={saving}
          className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)]
                     bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors"
        >
          Cancel
        </button>
        {onEditOverrides && (
          <button
            onClick={onEditOverrides}
            className="ml-auto px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)]
                       bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors"
          >
            User Preferences
          </button>
        )}
      </div>
    </>
  );
};
