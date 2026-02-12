import { ReactNode } from 'react';
import { AdminUser, DownloadDefaults } from '../../../services/api';
import { PasswordFieldConfig, SelectFieldConfig, SelectOption, TextFieldConfig } from '../../../types/settings';
import { PasswordField, SelectField, TextField } from '../fields';
import { FieldWrapper } from '../shared';
import { CreateUserFormState } from './types';

const UserCardShell = ({ title, children }: { title: string; children: ReactNode }) => (
  <div className="space-y-5 p-4 rounded-lg border border-[var(--border-muted)] bg-[var(--bg)]">
    <h3 className="text-sm font-medium">{title}</h3>
    {children}
  </div>
);

const CREATE_ROLE_OPTIONS: SelectOption[] = [
  { value: 'user', label: 'User' },
  { value: 'admin', label: 'Admin' },
];

const EDIT_ROLE_OPTIONS: SelectOption[] = [
  { value: 'admin', label: 'Admin' },
  { value: 'user', label: 'User' },
];

const createTextField = (
  key: string,
  label: string,
  value: string,
  placeholder: string,
  required = false,
): TextFieldConfig => ({
  type: 'TextField',
  key,
  label,
  value,
  placeholder,
  required,
});

const createPasswordField = (
  key: string,
  label: string,
  value: string,
  placeholder: string,
  required = false,
): PasswordFieldConfig => ({
  type: 'PasswordField',
  key,
  label,
  value,
  placeholder,
  required,
});

const createRoleField = (value: string, options: SelectOption[]): SelectFieldConfig => ({
  type: 'SelectField',
  key: 'role',
  label: 'Role',
  value,
  options,
});

const renderTextField = (
  field: TextFieldConfig,
  value: string,
  onChange: (value: string) => void,
  disabled = false,
  disabledReason?: string,
) => (
  <FieldWrapper field={field} disabledOverride={disabled} disabledReasonOverride={disabledReason}>
    <TextField field={field} value={value} onChange={onChange} disabled={disabled} />
  </FieldWrapper>
);

const renderSelectField = (
  field: SelectFieldConfig,
  value: string,
  onChange: (value: string) => void,
  disabled = false,
  disabledReason?: string,
) => (
  <FieldWrapper field={field} disabledOverride={disabled} disabledReasonOverride={disabledReason}>
    <SelectField field={field} value={value} onChange={onChange} disabled={disabled} />
  </FieldWrapper>
);

const renderPasswordField = (
  field: PasswordFieldConfig,
  value: string,
  onChange: (value: string) => void,
) => (
  <FieldWrapper field={field}>
    <PasswordField field={field} value={value} onChange={onChange} />
  </FieldWrapper>
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
  const usernameField = createTextField('username', 'Username', form.username, 'username', true);
  const displayNameField = createTextField('display_name', 'Display Name', form.display_name, 'Display name');
  const emailField = createTextField('email', 'Email', form.email, 'user@example.com');
  const passwordField = createPasswordField('password', 'Password', form.password, 'Min 4 characters', true);
  const roleField = createRoleField(form.role, CREATE_ROLE_OPTIONS);

  return (
    <UserCardShell title="Create Local User">
      {isFirstUser && (
        <p className="text-xs text-zinc-500">
          This will be the first account and will be created as admin.
        </p>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {renderTextField(usernameField, form.username, (value) => onChange({ ...form, username: value }))}
        {renderTextField(displayNameField, form.display_name, (value) => onChange({ ...form, display_name: value }))}
        {renderTextField(emailField, form.email, (value) => onChange({ ...form, email: value }))}
        {renderPasswordField(passwordField, form.password, (value) => onChange({ ...form, password: value }))}
      </div>

      {renderSelectField(roleField, form.role, (value) => onChange({ ...form, role: value }))}

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={onSubmit}
          disabled={creating}
          className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {creating ? 'Creating...' : 'Create Local User'}
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
  onDelete?: () => void;
  onConfirmDelete?: () => void;
  onCancelDelete?: () => void;
  isDeletePending?: boolean;
  deleting?: boolean;
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
  onDelete,
  onConfirmDelete,
  onCancelDelete,
  isDeletePending = false,
  deleting = false,
}: UserEditFieldsProps) => {
  const capabilities = user.edit_capabilities;
  const { authSource, canSetPassword, canEditRole, canEditEmail, canEditDisplayName } = capabilities;

  const displayNameField = createTextField('display_name', 'Display Name', user.display_name || '', 'Display name');
  const emailField = createTextField('email', 'Email', user.email || '', 'user@example.com');
  const roleField = createRoleField(user.role, EDIT_ROLE_OPTIONS);
  const newPasswordField = createPasswordField('new_password', 'New Password', editPassword, 'Leave empty to keep current');
  const confirmPasswordField = createPasswordField('confirm_password', 'Confirm Password', editPasswordConfirm, 'Confirm new password', true);

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
      {renderTextField(
        displayNameField,
        user.display_name || '',
        (value) => onUserChange({ ...user, display_name: value || null }),
        !canEditDisplayName,
        displayNameDisabledReason,
      )}

      {renderTextField(
        emailField,
        user.email || '',
        (value) => onUserChange({ ...user, email: value || null }),
        !canEditEmail,
        emailDisabledReason,
      )}

      {renderSelectField(
        roleField,
        user.role,
        (value) => onUserChange({ ...user, role: value }),
        !canEditRole,
        roleDisabledReason,
      )}

      {canSetPassword && (
        <>
          <div className="border-t border-[var(--border-muted)] pt-4">
            <p className="text-xs font-medium opacity-60 mb-3">Change Password</p>
          </div>

          {renderPasswordField(newPasswordField, editPassword, onEditPasswordChange)}

          {editPassword && renderPasswordField(confirmPasswordField, editPasswordConfirm, onEditPasswordConfirmChange)}
        </>
      )}

      <div className="flex flex-col gap-2 pt-3 border-t border-[var(--border-muted)] sm:flex-row sm:items-center">
        <div className="flex flex-wrap gap-2">
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
        </div>
        {onDelete && (
          <div className="flex flex-wrap gap-2 sm:ml-auto">
            {isDeletePending ? (
              <>
                <button
                  onClick={onConfirmDelete}
                  disabled={deleting}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-red-600 hover:bg-red-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {deleting ? 'Deleting...' : 'Confirm Delete'}
                </button>
                <button
                  onClick={onCancelDelete}
                  disabled={deleting}
                  className="px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)]
                             bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                onClick={onDelete}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-colors
                           border border-red-500/40 text-red-600 hover:bg-red-500/10"
              >
                Delete User
              </button>
            )}
          </div>
        )}
      </div>
    </>
  );
};
