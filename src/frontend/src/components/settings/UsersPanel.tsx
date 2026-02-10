import { useCallback, useEffect, useState } from 'react';
import {
  AdminUser,
  BookloreOption,
  DownloadDefaults,
  getAdminUsers,
  getAdminUser,
  getBookloreOptions,
  getDownloadDefaults,
  createAdminUser,
  updateAdminUser,
  deleteAdminUser,
} from '../../services/api';

interface UsersPanelProps {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

const inputClasses =
  'w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500 transition-colors';

const disabledInputClasses =
  'w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm opacity-50 cursor-not-allowed';

interface PerUserSettings {
  destination?: string;
  booklore_library_id?: string;
  booklore_path_id?: string;
  email_recipients?: Array<{ nickname: string; email: string }>;
}

export const UsersPanel = ({ onShowToast }: UsersPanelProps) => {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createForm, setCreateForm] = useState({ username: '', email: '', password: '', display_name: '', role: 'user' });
  const [creating, setCreating] = useState(false);

  // Edit view state
  const [editPassword, setEditPassword] = useState('');
  const [editPasswordConfirm, setEditPasswordConfirm] = useState('');
  const [downloadDefaults, setDownloadDefaults] = useState<DownloadDefaults | null>(null);
  const [userSettings, setUserSettings] = useState<PerUserSettings>({});
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [bookloreLibraries, setBookloreLibraries] = useState<BookloreOption[]>([]);
  const [booklorePaths, setBooklorePaths] = useState<BookloreOption[]>([]);

  const fetchUsers = useCallback(async () => {
    try {
      setLoading(true);
      setLoadError(null);
      const data = await getAdminUsers();
      setUsers(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load users';
      setLoadError(msg);
      onShowToast?.(msg, 'error');
    } finally {
      setLoading(false);
    }
  }, [onShowToast]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const startEditing = useCallback(async (user: AdminUser) => {
    setEditingUser({ ...user });
    setEditPassword('');
    setEditPasswordConfirm('');

    // Fetch full user data (with settings) and download defaults in parallel
    try {
      const [fullUser, defaults] = await Promise.all([
        getAdminUser(user.id),
        getDownloadDefaults(),
      ]);
      setDownloadDefaults(defaults);
      const settings = (fullUser.settings || {}) as PerUserSettings;
      setUserSettings(settings);

      // Fetch BookLore options if in booklore mode
      if (defaults.BOOKS_OUTPUT_MODE === 'booklore') {
        try {
          const blOptions = await getBookloreOptions();
          setBookloreLibraries(blOptions.libraries || []);
          setBooklorePaths(blOptions.paths || []);
        } catch {
          setBookloreLibraries([]);
          setBooklorePaths([]);
        }
      }

      // Set override toggles based on which settings exist
      setOverrides({
        destination: !!settings.destination,
        booklore_library_id: !!settings.booklore_library_id,
        booklore_path_id: !!settings.booklore_path_id,
        email_recipients: !!settings.email_recipients?.length,
      });
    } catch {
      setDownloadDefaults(null);
      setUserSettings({});
      setOverrides({});
    }
  }, []);

  const handleDelete = async (userId: number) => {
    try {
      await deleteAdminUser(userId);
      setConfirmDelete(null);
      onShowToast?.('User deleted', 'success');
      fetchUsers();
    } catch {
      onShowToast?.('Failed to delete user', 'error');
    }
  };

  const handleSaveEdit = async () => {
    if (!editingUser) return;

    // Validate password if provided
    if (editPassword) {
      if (editPassword.length < 4) {
        onShowToast?.('Password must be at least 4 characters', 'error');
        return;
      }
      if (editPassword !== editPasswordConfirm) {
        onShowToast?.('Passwords do not match', 'error');
        return;
      }
    }

    // Build settings payload: include overridden values, null out cleared overrides
    const settingsPayload: Record<string, unknown> = {};
    if (overrides.destination) {
      settingsPayload.destination = userSettings.destination || '';
    } else {
      settingsPayload.destination = null;
    }
    if (overrides.booklore_library_id) {
      settingsPayload.booklore_library_id = userSettings.booklore_library_id || '';
    } else {
      settingsPayload.booklore_library_id = null;
    }
    if (overrides.booklore_path_id) {
      settingsPayload.booklore_path_id = userSettings.booklore_path_id || '';
    } else {
      settingsPayload.booklore_path_id = null;
    }
    if (overrides.email_recipients) {
      settingsPayload.email_recipients = userSettings.email_recipients || [];
    } else {
      settingsPayload.email_recipients = null;
    }

    try {
      await updateAdminUser(editingUser.id, {
        email: editingUser.email,
        display_name: editingUser.display_name,
        role: editingUser.role,
        ...(editPassword ? { password: editPassword } : {}),
        ...(Object.keys(settingsPayload).length ? { settings: settingsPayload } : {}),
      });
      setEditingUser(null);
      onShowToast?.('User updated', 'success');
      fetchUsers();
    } catch {
      onShowToast?.('Failed to update user', 'error');
    }
  };

  const handleCreate = async () => {
    if (!createForm.username || !createForm.password) {
      onShowToast?.('Username and password are required', 'error');
      return;
    }
    setCreating(true);
    try {
      const data = await createAdminUser(createForm as { username: string; password: string; email?: string; display_name?: string; role?: string });
      setShowCreateForm(false);
      setCreateForm({ username: '', email: '', password: '', display_name: '', role: 'user' });
      onShowToast?.(`User ${data.username} created`, 'success');
      fetchUsers();
    } catch (err) {
      onShowToast?.((err as Error).message || 'Failed to create user', 'error');
    } finally {
      setCreating(false);
    }
  };

  const toggleOverride = (key: string, enabled: boolean) => {
    setOverrides((prev) => ({ ...prev, [key]: enabled }));
    if (!enabled) {
      setUserSettings((prev) => {
        const next = { ...prev };
        (next as Record<string, unknown>)[key] = null;
        return next;
      });
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

  // Edit view
  if (editingUser) {
    const outputMode = downloadDefaults?.BOOKS_OUTPUT_MODE || 'folder';

    return (
      <div className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => setEditingUser(null)}
            className="text-sm opacity-60 hover:opacity-100 transition-opacity"
          >
            &larr; Back
          </button>
          <h3 className="text-sm font-medium">Edit {editingUser.username}</h3>
        </div>

        <div className="space-y-5 max-w-lg">
          {editingUser.oidc_subject && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs bg-sky-500/10 text-sky-400">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
                <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a.75.75 0 000 1.5h.253a.25.25 0 01.244.304l-.459 2.066A1.75 1.75 0 0010.747 15H11a.75.75 0 000-1.5h-.253a.25.25 0 01-.244-.304l.459-2.066A1.75 1.75 0 009.253 9H9z" clipRule="evenodd" />
              </svg>
              This user authenticates via SSO. Password is managed by the identity provider.
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-sm font-medium">Display Name</label>
            <input
              type="text"
              value={editingUser.display_name || ''}
              onChange={(e) => setEditingUser({ ...editingUser, display_name: e.target.value || null })}
              className={inputClasses}
              placeholder="Display name"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium">Email</label>
            <input
              type="email"
              value={editingUser.email || ''}
              onChange={(e) => setEditingUser({ ...editingUser, email: e.target.value || null })}
              className={inputClasses}
              placeholder="user@example.com"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium">Role</label>
            <select
              value={editingUser.role}
              onChange={(e) => setEditingUser({ ...editingUser, role: e.target.value })}
              className={inputClasses}
            >
              <option value="admin">Admin</option>
              <option value="user">User</option>
            </select>
            {/* Warn if setting OIDC user to admin while admin_group is configured */}
            {editingUser.oidc_subject && downloadDefaults?.OIDC_ADMIN_GROUP && (
              <div className="flex items-start gap-2 p-2 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs mt-2">
                <svg className="w-4 h-4 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span>
                  Admin role for OIDC accounts is managed by your identity provider ({downloadDefaults.OIDC_ADMIN_GROUP} group). Local users can be set as admin directly.
                </span>
              </div>
            )}
          </div>

          {/* Password section */}
          {!editingUser.oidc_subject && (
            <>
              <div className="border-t border-[var(--border-muted)] pt-4">
                <p className="text-xs font-medium opacity-60 mb-3">Change Password</p>
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium">New Password</label>
                <input
                  type="password"
                  value={editPassword}
                  onChange={(e) => setEditPassword(e.target.value)}
                  className={inputClasses}
                  placeholder="Leave empty to keep current"
                />
              </div>
              {editPassword && (
                <div className="space-y-1.5">
                  <label className="text-sm font-medium">Confirm Password</label>
                  <input
                    type="password"
                    value={editPasswordConfirm}
                    onChange={(e) => setEditPasswordConfirm(e.target.value)}
                    className={inputClasses}
                    placeholder="Confirm new password"
                  />
                </div>
              )}
            </>
          )}

          {/* Per-user download settings overrides */}
          {downloadDefaults && (
            <>
              <div className="border-t border-[var(--border-muted)] pt-4">
                <p className="text-xs font-medium opacity-60 mb-1">Download Settings Overrides</p>
                <p className="text-xs opacity-40 mb-3">Override global defaults for this user.</p>
              </div>

              {/* Destination override (shown for folder mode) */}
              {(outputMode === 'folder' || outputMode === 'booklore') && (
                <OverrideField
                  label="Destination Folder"
                  enabled={overrides.destination || false}
                  onToggle={(v) => toggleOverride('destination', v)}
                  globalValue={downloadDefaults.DESTINATION || '/books'}
                >
                  <input
                    type="text"
                    value={userSettings.destination || ''}
                    onChange={(e) => setUserSettings((s) => ({ ...s, destination: e.target.value }))}
                    className={overrides.destination ? inputClasses : disabledInputClasses}
                    disabled={!overrides.destination}
                    placeholder={downloadDefaults.DESTINATION || '/books'}
                  />
                </OverrideField>
              )}

              {/* BookLore overrides */}
              {outputMode === 'booklore' && (
                <>
                  <OverrideField
                    label="BookLore Library"
                    enabled={overrides.booklore_library_id || false}
                    onToggle={(v) => toggleOverride('booklore_library_id', v)}
                    globalValue={
                      bookloreLibraries.find((l) => l.value === downloadDefaults.BOOKLORE_LIBRARY_ID)?.label
                      || downloadDefaults.BOOKLORE_LIBRARY_ID
                      || 'Not set'
                    }
                  >
                    <select
                      value={userSettings.booklore_library_id || ''}
                      onChange={(e) => {
                        setUserSettings((s) => ({ ...s, booklore_library_id: e.target.value, booklore_path_id: '' }));
                        // Reset path override when library changes
                        if (overrides.booklore_path_id) {
                          setOverrides((o) => ({ ...o, booklore_path_id: true }));
                        }
                      }}
                      className={overrides.booklore_library_id ? inputClasses : disabledInputClasses}
                      disabled={!overrides.booklore_library_id}
                    >
                      <option value="">Select library...</option>
                      {bookloreLibraries.map((lib) => (
                        <option key={lib.value} value={lib.value}>{lib.label}</option>
                      ))}
                    </select>
                  </OverrideField>
                  <OverrideField
                    label="BookLore Path"
                    enabled={overrides.booklore_path_id || false}
                    onToggle={(v) => toggleOverride('booklore_path_id', v)}
                    globalValue={
                      booklorePaths.find((p) => p.value === downloadDefaults.BOOKLORE_PATH_ID)?.label
                      || downloadDefaults.BOOKLORE_PATH_ID
                      || 'Not set'
                    }
                  >
                    <select
                      value={userSettings.booklore_path_id || ''}
                      onChange={(e) => setUserSettings((s) => ({ ...s, booklore_path_id: e.target.value }))}
                      className={overrides.booklore_path_id ? inputClasses : disabledInputClasses}
                      disabled={!overrides.booklore_path_id}
                    >
                      <option value="">Select path...</option>
                      {booklorePaths
                        .filter((p) => {
                          const selectedLib = userSettings.booklore_library_id || downloadDefaults.BOOKLORE_LIBRARY_ID;
                          return !p.childOf || p.childOf === selectedLib;
                        })
                        .map((path) => (
                          <option key={path.value} value={path.value}>{path.label}</option>
                        ))}
                    </select>
                  </OverrideField>
                </>
              )}

              {/* Email recipients override */}
              {outputMode === 'email' && (
                <OverrideField
                  label="Email Recipients"
                  enabled={overrides.email_recipients || false}
                  onToggle={(v) => toggleOverride('email_recipients', v)}
                  globalValue={
                    downloadDefaults.EMAIL_RECIPIENTS?.length
                      ? downloadDefaults.EMAIL_RECIPIENTS.map((r) => r.nickname || r.email).join(', ')
                      : 'None configured'
                  }
                >
                  {overrides.email_recipients && (
                    <EmailRecipientsEditor
                      recipients={userSettings.email_recipients || []}
                      onChange={(r) => setUserSettings((s) => ({ ...s, email_recipients: r }))}
                    />
                  )}
                </OverrideField>
              )}
            </>
          )}

          <div className="flex gap-2 pt-2">
            <button
              onClick={handleSaveEdit}
              className="px-4 py-2.5 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors"
            >
              Save Changes
            </button>
            <button
              onClick={() => setEditingUser(null)}
              className="px-4 py-2.5 rounded-lg text-sm font-medium border border-[var(--border-muted)]
                         bg-[var(--bg-soft)] hover:bg-[var(--hover-surface)] transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    );
  }

  // List view
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-4">
        <p className="text-xs opacity-60">
          Users are created automatically via OIDC login, or manually below.
        </p>
        <button
          onClick={() => setShowCreateForm(!showCreateForm)}
          className="px-3 py-1.5 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors shrink-0"
        >
          {showCreateForm ? 'Cancel' : 'Create User'}
        </button>
      </div>

      {showCreateForm && (
        <div className="mb-4 p-4 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] space-y-3">
          {users.length === 0 && (
            <p className="text-xs opacity-60 pb-1">
              This will be the first account and will be created as admin.
            </p>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Username <span className="text-red-500">*</span></label>
              <input
                type="text"
                value={createForm.username}
                onChange={(e) => setCreateForm({ ...createForm, username: e.target.value })}
                className={inputClasses}
                placeholder="username"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Display Name</label>
              <input
                type="text"
                value={createForm.display_name}
                onChange={(e) => setCreateForm({ ...createForm, display_name: e.target.value })}
                className={inputClasses}
                placeholder="Display Name"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Email</label>
              <input
                type="email"
                value={createForm.email}
                onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                className={inputClasses}
                placeholder="user@example.com"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Password <span className="text-red-500">*</span></label>
              <input
                type="password"
                value={createForm.password}
                onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                className={inputClasses}
                placeholder="Min 4 characters"
              />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <select
              value={createForm.role}
              onChange={(e) => setCreateForm({ ...createForm, role: e.target.value })}
              className="px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm transition-colors"
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
            <button
              onClick={handleCreate}
              disabled={creating}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {creating ? 'Creating...' : 'Create'}
            </button>
          </div>
        </div>
      )}

      {users.length === 0 ? (
        <div className="text-center py-8 space-y-2">
          <p className="text-sm opacity-50">No users yet.</p>
          <p className="text-xs opacity-40">
            Create a local admin account before enabling OIDC to avoid getting locked out.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {users.map((user) => (
            <div
              key={user.id}
              className="flex items-center justify-between p-3 rounded-lg border border-[var(--border-muted)]
                         bg-[var(--bg-soft)] transition-colors"
            >
              <div className="flex items-center gap-3 min-w-0 flex-1">
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium shrink-0
                    ${user.role === 'admin' ? 'bg-sky-500/20 text-sky-400' : 'bg-zinc-500/20'}`}
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
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded font-medium
                        ${user.oidc_subject
                          ? 'bg-sky-500/15 text-sky-400'
                          : 'bg-zinc-500/15 opacity-70'}`}
                    >
                      {user.oidc_subject ? 'OIDC' : 'Password'}
                    </span>
                  </div>
                  <div className="text-xs opacity-50 truncate">
                    {user.email || 'No email'}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <span
                  className={`text-xs px-2 py-0.5 rounded font-medium
                    ${user.role === 'admin' ? 'bg-sky-500/15 text-sky-400' : 'bg-zinc-500/10 opacity-70'}`}
                >
                  {user.role}
                </span>

                <button
                  onClick={() => startEditing(user)}
                  className="text-xs px-2 py-1 rounded border border-[var(--border-muted)]
                             hover:bg-[var(--hover-surface)] transition-colors"
                >
                  Edit
                </button>

                {confirmDelete === user.id ? (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => handleDelete(user.id)}
                      className="text-xs px-2 py-1 rounded bg-red-600 text-white hover:bg-red-700 transition-colors"
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => setConfirmDelete(null)}
                      className="text-xs px-2 py-1 rounded border border-[var(--border-muted)]
                                 hover:bg-[var(--hover-surface)] transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmDelete(user.id)}
                    className="text-xs px-2 py-1 rounded border border-[var(--border-muted)] text-red-400
                               hover:bg-red-600 hover:text-white hover:border-red-600 transition-colors"
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface OverrideFieldProps {
  label: string;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  globalValue: string;
  children: React.ReactNode;
}

const OverrideField = ({ label, enabled, onToggle, globalValue, children }: OverrideFieldProps) => (
  <div className="space-y-1.5">
    <div className="flex items-center justify-between">
      <label className="text-sm font-medium">{label}</label>
      <button
        type="button"
        onClick={() => onToggle(!enabled)}
        className={`text-[10px] px-2 py-0.5 rounded font-medium transition-colors
          ${enabled
            ? 'bg-sky-500/15 text-sky-400 hover:bg-sky-500/25'
            : 'bg-zinc-500/10 opacity-60 hover:opacity-80'}`}
      >
        {enabled ? 'Custom' : 'Global'}
      </button>
    </div>
    {!enabled && (
      <p className="text-xs opacity-40">Using global: {globalValue}</p>
    )}
    {children}
  </div>
);

interface EmailRecipientsEditorProps {
  recipients: Array<{ nickname: string; email: string }>;
  onChange: (recipients: Array<{ nickname: string; email: string }>) => void;
}

const EmailRecipientsEditor = ({ recipients, onChange }: EmailRecipientsEditorProps) => {
  const addRecipient = () => {
    onChange([...recipients, { nickname: '', email: '' }]);
  };

  const removeRecipient = (index: number) => {
    onChange(recipients.filter((_, i) => i !== index));
  };

  const updateRecipient = (index: number, field: 'nickname' | 'email', value: string) => {
    const updated = [...recipients];
    updated[index] = { ...updated[index], [field]: value };
    onChange(updated);
  };

  return (
    <div className="space-y-2">
      {recipients.map((r, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type="text"
            value={r.nickname}
            onChange={(e) => updateRecipient(i, 'nickname', e.target.value)}
            className={inputClasses}
            placeholder="Nickname"
          />
          <input
            type="email"
            value={r.email}
            onChange={(e) => updateRecipient(i, 'email', e.target.value)}
            className={inputClasses}
            placeholder="email@example.com"
          />
          <button
            type="button"
            onClick={() => removeRecipient(i)}
            className="text-xs px-2 py-1 rounded text-red-400 hover:bg-red-600 hover:text-white transition-colors shrink-0"
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={addRecipient}
        className="text-xs px-2 py-1 rounded border border-[var(--border-muted)]
                   hover:bg-[var(--hover-surface)] transition-colors"
      >
        + Add Recipient
      </button>
    </div>
  );
};
