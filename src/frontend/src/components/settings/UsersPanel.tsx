import { useCallback, useEffect, useState } from 'react';
import { AdminUser, getAdminUsers, updateAdminUser, deleteAdminUser } from '../../services/api';
import { getApiBase } from '../../utils/basePath';

interface UsersPanelProps {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

const inputClasses =
  'w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500 transition-colors';

export const UsersPanel = ({ onShowToast }: UsersPanelProps) => {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createForm, setCreateForm] = useState({ username: '', email: '', password: '', display_name: '', role: 'user' });
  const [creating, setCreating] = useState(false);

  const fetchUsers = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getAdminUsers();
      setUsers(data);
    } catch {
      onShowToast?.('Failed to load users', 'error');
    } finally {
      setLoading(false);
    }
  }, [onShowToast]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

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
    try {
      await updateAdminUser(editingUser.id, {
        email: editingUser.email,
        display_name: editingUser.display_name,
        role: editingUser.role,
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
      const API_BASE = getApiBase();
      const res = await fetch(`${API_BASE}/admin/users`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(createForm),
      });
      const data = await res.json();
      if (!res.ok) {
        onShowToast?.(data.error || 'Failed to create user', 'error');
        return;
      }
      setShowCreateForm(false);
      setCreateForm({ username: '', email: '', password: '', display_name: '', role: 'user' });
      onShowToast?.(`User ${data.username} created`, 'success');
      fetchUsers();
    } catch {
      onShowToast?.('Failed to create user', 'error');
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm opacity-60 p-8">
        Loading users...
      </div>
    );
  }

  // Edit view
  if (editingUser) {
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
              This user authenticates via SSO only. Password cannot be set.
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
          </div>

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
                  onClick={() => setEditingUser({ ...user })}
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
