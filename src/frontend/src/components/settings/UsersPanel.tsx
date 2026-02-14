import { useEffect, useMemo } from 'react';
import { AdminUser } from '../../services/api';
import { ActionResult, SettingsTab, TableFieldConfig } from '../../types/settings';
import {
  canCreateLocalUsersForAuthMode,
  RequestPolicyGrid,
  normalizeRequestPolicyDefaults,
  normalizeRequestPolicyRules,
  parseSourceCapabilitiesFromRulesField,
  UserListView,
  UserOverridesView,
  useUserForm,
  useUserMutations,
  useUsersFetch,
  useUsersPanelState,
} from './users';
import type { RequestPolicyContentType, RequestPolicyMode } from './users';
import { SettingsContent } from './SettingsContent';
import { SettingsSaveBar } from './shared';

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
    if (route.kind === 'create' && !canCreateLocalUsers) {
      backToList();
    }
  }, [backToList, canCreateLocalUsers, route.kind]);

  const handleSaveUserEdit = async () => {
    const ok = await saveEditedUser({ includeSettings: false });
    if (ok) {
      backToList();
    }
  };

  const handleSaveUserOverrides = async () => {
    const ok = await saveEditedUser({
      includeProfile: false,
      includePassword: false,
      includeSettings: true,
    });
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
      <div className="flex-1 flex flex-col min-h-0">
        <UserOverridesView
          hasChanges={hasUserSettingsChanges}
          onBack={handleBackToEdit}
          deliveryPreferences={deliveryPreferences}
          isUserOverridable={isUserOverridable}
          userSettings={userSettings}
          setUserSettings={(updater) => setUserSettings(updater)}
          usersTab={tab}
          globalUsersSettingsValues={values}
        />

        {hasUserSettingsChanges && (
          <SettingsSaveBar onSave={handleSaveUserOverrides} isSaving={saving} />
        )}
      </div>
    );
  }

  const requestRulesField = tab.fields.find(
    (field): field is TableFieldConfig =>
      field.key === 'REQUEST_POLICY_RULES' && field.type === 'TableField'
  );
  const hasRequestPolicyGrid = Boolean(requestRulesField);

  const globalRequestDefaults = useMemo(
    () =>
      normalizeRequestPolicyDefaults({
        ebook: values.REQUEST_POLICY_DEFAULT_EBOOK,
        audiobook: values.REQUEST_POLICY_DEFAULT_AUDIOBOOK,
      }),
    [values.REQUEST_POLICY_DEFAULT_EBOOK, values.REQUEST_POLICY_DEFAULT_AUDIOBOOK]
  );

  const explicitGlobalRules = useMemo(
    () => normalizeRequestPolicyRules(values.REQUEST_POLICY_RULES),
    [values.REQUEST_POLICY_RULES]
  );

  const requestSourceCapabilities = useMemo(
    () =>
      parseSourceCapabilitiesFromRulesField(
        requestRulesField,
        explicitGlobalRules.map((row) => row.source)
      ),
    [requestRulesField, explicitGlobalRules]
  );

  const requestRulesIndex = tab.fields.findIndex((field) => field.key === 'REQUEST_POLICY_RULES');
  const shouldSplitRequestPolicyFields = hasRequestPolicyGrid && requestRulesIndex >= 0;
  const requestPolicyFieldKeys = new Set([
    'REQUEST_POLICY_DEFAULT_EBOOK',
    'REQUEST_POLICY_DEFAULT_AUDIOBOOK',
    'REQUEST_POLICY_RULES',
  ]);

  const beforeRequestGridTab: SettingsTab = shouldSplitRequestPolicyFields
    ? {
        ...tab,
        fields: tab.fields.filter(
          (field, index) =>
            index <= requestRulesIndex && !requestPolicyFieldKeys.has(field.key)
        ),
      }
    : tab;

  const afterRequestGridTab: SettingsTab | null = shouldSplitRequestPolicyFields
    ? {
        ...tab,
        fields: tab.fields.filter(
          (field, index) =>
            index > requestRulesIndex && !requestPolicyFieldKeys.has(field.key)
        ),
      }
    : null;

  const onGlobalDefaultModeChange = (contentType: RequestPolicyContentType, mode: RequestPolicyMode) => {
    const key =
      contentType === 'ebook' ? 'REQUEST_POLICY_DEFAULT_EBOOK' : 'REQUEST_POLICY_DEFAULT_AUDIOBOOK';
    onChange(key, mode);
  };

  const onGlobalRulesChange = (rules: Array<{ source: string; content_type: 'ebook' | 'audiobook'; mode: 'download' | 'request_release' | 'blocked' }>) => {
    onChange('REQUEST_POLICY_RULES', rules);
  };

  const defaultEbookField = tab.fields.find((field) => field.key === 'REQUEST_POLICY_DEFAULT_EBOOK');
  const defaultAudioField = tab.fields.find((field) => field.key === 'REQUEST_POLICY_DEFAULT_AUDIOBOOK');

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div
        className="flex-1 overflow-y-auto p-6"
        style={{ paddingBottom: hasChanges ? 'calc(5rem + env(safe-area-inset-bottom))' : '1.5rem' }}
      >
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
            onDelete={deleteUser}
            deletingUserId={deletingUserId}
            onSyncCwa={handleSyncCwa}
            syncingCwa={syncingCwa}
          />

          <div className="pt-5 mt-4 border-t border-black/10 dark:border-white/10">
            <SettingsContent
              tab={beforeRequestGridTab}
              values={values}
              onChange={onChange}
              onSave={onSave}
              onAction={onAction}
              isSaving={isSaving}
              hasChanges={false}
              embedded
            />

            {shouldSplitRequestPolicyFields && (
              <div className="space-y-2 pt-5">
                {requestRulesField?.description && (
                  <p className="text-xs opacity-60">{requestRulesField.description}</p>
                )}
                <RequestPolicyGrid
                  defaultModes={globalRequestDefaults}
                  onDefaultModeChange={onGlobalDefaultModeChange}
                  defaultModeDisabled={{
                    ebook: Boolean(defaultEbookField && 'fromEnv' in defaultEbookField && defaultEbookField.fromEnv),
                    audiobook: Boolean(defaultAudioField && 'fromEnv' in defaultAudioField && defaultAudioField.fromEnv),
                  }}
                  explicitRules={explicitGlobalRules}
                  onExplicitRulesChange={onGlobalRulesChange}
                  sourceCapabilities={requestSourceCapabilities}
                  rulesDisabled={Boolean(requestRulesField && 'fromEnv' in requestRulesField && requestRulesField.fromEnv)}
                />
              </div>
            )}

            {afterRequestGridTab && afterRequestGridTab.fields.length > 0 && (
              <div className="pt-5">
                <SettingsContent
                  tab={afterRequestGridTab}
                  values={values}
                  onChange={onChange}
                  onSave={onSave}
                  onAction={onAction}
                  isSaving={isSaving}
                  hasChanges={false}
                  embedded
                />
              </div>
            )}
          </div>
        </div>
      </div>

      {hasChanges && (
        <SettingsSaveBar onSave={onSave} isSaving={isSaving} />
      )}
    </div>
  );
};
