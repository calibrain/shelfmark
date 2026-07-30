import { useCallback, useState } from 'react';

import { useMediaQuery } from '../../hooks/useMediaQuery';
import { useMountEffect } from '../../hooks/useMountEffect';
import { useSettings } from '../../hooks/useSettings';
import { getSettingsTab } from '../../services/api';
import { SettingsContent } from './SettingsContent';
import { SettingsHeader } from './SettingsHeader';
import { SettingsSidebar } from './SettingsSidebar';

interface AdminSettingsPageProps {
  authMode: string;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved?: () => void;
  onRefreshAuth?: () => Promise<void>;
}

const stringValue = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback;
const stringArrayValue = (value: unknown): string[] =>
  Array.isArray(value) && value.every((entry) => typeof entry === 'string') ? value : [];

const SettingsTabSync = ({
  selectedTab,
  setSecurityAccessError,
}: {
  selectedTab: string;
  setSecurityAccessError: (message: string | null) => void;
}) => {
  useMountEffect(() => {
    let cancelled = false;
    if (selectedTab !== 'security') {
      setSecurityAccessError(null);
    } else {
      void getSettingsTab('security')
        .then(() => !cancelled && setSecurityAccessError(null))
        .catch((error) => {
          if (!cancelled) {
            const message =
              error instanceof Error ? error.message : 'Failed to load security settings';
            setSecurityAccessError(
              message.toLowerCase().includes('admin access required') ? message : null,
            );
          }
        });
    }
    return () => {
      cancelled = true;
    };
  });
  return null;
};

export const AdminSettingsPage = ({
  authMode,
  onShowToast,
  onSettingsSaved,
  onRefreshAuth,
}: AdminSettingsPageProps) => {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const [showMobileDetail, setShowMobileDetail] = useState(false);
  const [securityAccessError, setSecurityAccessError] = useState<string | null>(null);
  const {
    tabs,
    groups,
    isLoading,
    error,
    selectedTab,
    setSelectedTab,
    values,
    updateValue,
    hasChanges,
    saveTab,
    executeAction,
    isSaving,
  } = useSettings();

  const selectTab = useCallback(
    (tab: string) => {
      setSelectedTab(tab);
      if (isMobile) setShowMobileDetail(true);
    },
    [isMobile, setSelectedTab],
  );
  const save = useCallback(async () => {
    if (!selectedTab) return;
    const result = await saveTab(selectedTab);
    onShowToast?.(result.message, result.success ? 'success' : 'error');
    if (!result.success) return;
    onSettingsSaved?.();
    if (result.requiresRestart) {
      setTimeout(
        () => onShowToast?.('Some settings require a container restart to take effect', 'info'),
        500,
      );
    }
  }, [onSettingsSaved, onShowToast, saveTab, selectedTab]);
  const change = useCallback(
    (key: string, value: unknown) => {
      if (!selectedTab) return;
      updateValue(selectedTab, key, value);
      if (selectedTab !== 'security') return;
      const tabValues = values[selectedTab] || {};
      const scopes = stringArrayValue(tabValues['OIDC_SCOPES']);
      if (key === 'OIDC_USE_ADMIN_GROUP') {
        const claim = stringValue(tabValues['OIDC_GROUP_CLAIM'], 'groups');
        if (value === true && !scopes.includes(claim))
          updateValue(selectedTab, 'OIDC_SCOPES', [...scopes, claim]);
        if (value === false && scopes.includes(claim))
          updateValue(
            selectedTab,
            'OIDC_SCOPES',
            scopes.filter((scope) => scope !== claim),
          );
      }
      if (
        key === 'OIDC_GROUP_CLAIM' &&
        typeof value === 'string' &&
        tabValues['OIDC_USE_ADMIN_GROUP'] === true
      ) {
        const priorClaim = stringValue(tabValues['OIDC_GROUP_CLAIM'], 'groups');
        const nextScopes = scopes.filter((scope) => scope !== priorClaim);
        if (value && !nextScopes.includes(value)) nextScopes.push(value);
        updateValue(selectedTab, 'OIDC_SCOPES', nextScopes);
      }
    },
    [selectedTab, updateValue, values],
  );
  const action = useCallback(
    async (actionKey: string) => {
      if (!selectedTab) return { success: false, message: 'No tab selected' };
      if (selectedTab === 'security' && actionKey === 'open_users_tab') {
        selectTab('users');
        return { success: true, message: 'Opening Users tab...' };
      }
      return executeAction(selectedTab, actionKey);
    },
    [executeAction, selectTab, selectedTab],
  );
  const currentTab = tabs.find((tab) => tab.name === selectedTab);
  const content = currentTab ? (
    securityAccessError ? (
      <div className="flex flex-1 items-center justify-center p-8 text-sm opacity-60">
        {securityAccessError}
      </div>
    ) : (
      <SettingsContent
        tab={currentTab}
        values={values[currentTab.name] || {}}
        onChange={change}
        onSave={save}
        onAction={action}
        isSaving={isSaving}
        hasChanges={hasChanges(currentTab.name)}
        isUniversalMode={true}
        customFieldContext={{ authMode, onShowToast, onRefreshAuth, onSettingsSaved }}
      />
    )
  ) : (
    <div className="flex flex-1 items-center justify-center text-sm opacity-60">
      Select a category to configure
    </div>
  );

  if (isLoading) return <p className="py-12 text-center text-sm opacity-60">Loading settings...</p>;
  if (error) return <p className="py-12 text-center text-sm text-red-600">{error}</p>;

  const tabSync = selectedTab ? (
    <SettingsTabSync selectedTab={selectedTab} setSecurityAccessError={setSecurityAccessError} />
  ) : null;
  if (isMobile) {
    return (
      <div className="flex min-h-[calc(100vh-10rem)] flex-col">
        {tabSync}
        {showMobileDetail ? (
          <>
            <SettingsHeader
              title={currentTab?.displayName || 'Admin settings'}
              showBack
              onBack={() => setShowMobileDetail(false)}
            />
            {content}
          </>
        ) : (
          <>
            <SettingsHeader title="Admin settings" />
            <SettingsSidebar
              tabs={tabs}
              groups={groups}
              selectedTab={selectedTab}
              onSelectTab={selectTab}
              mode="list"
            />
          </>
        )}
      </div>
    );
  }
  return (
    <div className="mx-auto flex min-h-[calc(100vh-10rem)] max-w-6xl overflow-hidden rounded-xl border border-(--border-muted)">
      {tabSync}
      <SettingsSidebar
        tabs={tabs}
        groups={groups}
        selectedTab={selectedTab}
        onSelectTab={selectTab}
        mode="sidebar"
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <SettingsHeader title="Admin settings" />
        {content}
      </div>
    </div>
  );
};
