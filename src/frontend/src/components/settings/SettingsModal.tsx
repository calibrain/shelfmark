import { useEffect, useState, useCallback, useRef, useMemo } from 'react';

import { useSearchMode } from '../../contexts/SearchModeContext';
import { useSettings } from '../../hooks/useSettings';
import { getAdminSettingsOverridesSummary, getSettingsTab } from '../../services/api';
import { SettingsContent } from './SettingsContent';
import { SettingsHeader } from './SettingsHeader';
import { SettingsSidebar } from './SettingsSidebar';
import { primeUsersCache } from './users/useUsersFetch';

interface SettingsModalProps {
  isOpen: boolean;
  authMode: string;
  onClose: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved?: () => void;
  onRefreshAuth?: () => Promise<void>;
}

function getStringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function getStringArrayValue(value: unknown): string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === 'string') ? value : [];
}

function getBooleanValue(value: unknown): boolean {
  return typeof value === 'boolean' ? value : false;
}

export const SettingsModal = ({
  isOpen,
  authMode,
  onClose,
  onShowToast,
  onSettingsSaved,
  onRefreshAuth,
}: SettingsModalProps) => {
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

  const { isUniversalMode } = useSearchMode();

  // Track if we're showing detail view on mobile
  const [isMobile, setIsMobile] = useState(false);
  const [showMobileDetail, setShowMobileDetail] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [securityAccessError, setSecurityAccessError] = useState<string | null>(null);
  const [tabOverrideSummaries, setTabOverrideSummaries] = useState<
    Record<
      string,
      Record<
        string,
        { count: number; users: Array<{ userId: number; username: string; value: unknown }> }
      >
    >
  >({});

  // Track previous isOpen state to detect modal open transition
  const prevIsOpenRef = useRef(false);
  const overrideSummaryRequestIdRef = useRef(0);

  // Check for mobile viewport
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [onClose]);

  // Handle ESC key
  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (isMobile && showMobileDetail) {
          setShowMobileDetail(false);
        } else {
          handleClose();
        }
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, isMobile, showMobileDetail, handleClose]);

  // Prevent body scroll when open
  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

  // Reset mobile detail view when modal opens
  useEffect(() => {
    if (isOpen) {
      setShowMobileDetail(false);
      setIsClosing(false);
      setTabOverrideSummaries({});
      void primeUsersCache();
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen || selectedTab !== 'security') {
      setSecurityAccessError(null);
      return undefined;
    }

    let cancelled = false;
    getSettingsTab('security')
      .then(() => {
        if (cancelled) return;
        setSecurityAccessError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : 'Failed to load security settings';
        if (message.toLowerCase().includes('admin access required')) {
          setSecurityAccessError(message);
          return;
        }
        setSecurityAccessError(null);
      });

    return () => {
      cancelled = true;
    };
  }, [isOpen, selectedTab]);

  const refreshOverrideSummaryForTab = useCallback(async (tabName: string) => {
    const requestId = ++overrideSummaryRequestIdRef.current;
    try {
      const data = await getAdminSettingsOverridesSummary(tabName);
      if (overrideSummaryRequestIdRef.current !== requestId) {
        return;
      }
      setTabOverrideSummaries((prev) => ({
        ...prev,
        [tabName]: data.keys || {},
      }));
    } catch {
      if (overrideSummaryRequestIdRef.current !== requestId) {
        return;
      }
      setTabOverrideSummaries((prev) => ({
        ...prev,
        [tabName]: {},
      }));
    }
  }, []);

  useEffect(() => {
    if (!isOpen || !selectedTab) {
      return;
    }
    void refreshOverrideSummaryForTab(selectedTab);
  }, [isOpen, selectedTab, refreshOverrideSummaryForTab]);

  // Reset to first tab when modal transitions from closed to open
  useEffect(() => {
    const justOpened = isOpen && !prevIsOpenRef.current;
    prevIsOpenRef.current = isOpen;

    // On desktop, select first tab when modal opens (reset on each open)
    if (justOpened && !isMobile && tabs.length > 0) {
      setSelectedTab(tabs[0].name);
    }
  }, [isOpen, isMobile, tabs, setSelectedTab]);

  const handleSelectTab = useCallback(
    (tabName: string) => {
      setSelectedTab(tabName);
      if (isMobile) {
        setShowMobileDetail(true);
      }
    },
    [isMobile, setSelectedTab],
  );

  const handleBack = useCallback(() => {
    setShowMobileDetail(false);
  }, []);

  const handleRefreshCurrentTabOverrideSummary = useCallback(() => {
    if (!selectedTab) {
      return;
    }
    void refreshOverrideSummaryForTab(selectedTab);
  }, [selectedTab, refreshOverrideSummaryForTab]);

  const handleSave = useCallback(async () => {
    if (!selectedTab) return;
    const result = await saveTab(selectedTab);
    if (result.success) {
      void refreshOverrideSummaryForTab(selectedTab);
      onShowToast?.(result.message, 'success');
      // Notify parent that settings were saved so it can refresh config
      onSettingsSaved?.();
      // Show additional toast if some settings require restart
      if (result.requiresRestart) {
        setTimeout(() => {
          onShowToast?.('Some settings require a container restart to take effect', 'info');
        }, 500);
      }
    } else {
      onShowToast?.(result.message, 'error');
    }
  }, [selectedTab, saveTab, onShowToast, onSettingsSaved, refreshOverrideSummaryForTab]);

  const handleAction = useCallback(
    async (actionKey: string) => {
      if (!selectedTab) {
        return { success: false, message: 'No tab selected' };
      }

      if (selectedTab === 'security' && actionKey === 'open_users_tab') {
        setSelectedTab('users');
        if (isMobile) {
          setShowMobileDetail(true);
        }
        return { success: true, message: 'Opening Users tab...' };
      }
      const result = await executeAction(selectedTab, actionKey);
      if (result.success) {
        void refreshOverrideSummaryForTab(selectedTab);
      }
      return result;
    },
    [selectedTab, executeAction, isMobile, setSelectedTab, refreshOverrideSummaryForTab],
  );

  // Memoize the field change handler to prevent creating new functions on every render
  const handleFieldChange = useCallback(
    (key: string, value: unknown) => {
      if (!selectedTab) return;
      updateValue(selectedTab, key, value);

      // Auto-manage OIDC scopes when admin group settings change
      if (selectedTab === 'security') {
        const tabValues = values[selectedTab] || {};
        const currentScopes = getStringArrayValue(tabValues['OIDC_SCOPES']);

        if (key === 'OIDC_USE_ADMIN_GROUP') {
          const groupClaim = getStringValue(tabValues['OIDC_GROUP_CLAIM'], 'groups');
          if (value === true && !currentScopes.includes(groupClaim)) {
            updateValue(selectedTab, 'OIDC_SCOPES', [...currentScopes, groupClaim]);
          } else if (value === false && currentScopes.includes(groupClaim)) {
            updateValue(
              selectedTab,
              'OIDC_SCOPES',
              currentScopes.filter((s) => s !== groupClaim),
            );
          }
        }

        if (key === 'OIDC_GROUP_CLAIM' && typeof value === 'string') {
          const useAdminGroup = getBooleanValue(tabValues['OIDC_USE_ADMIN_GROUP']);
          if (useAdminGroup) {
            const oldClaim = getStringValue(tabValues['OIDC_GROUP_CLAIM'], 'groups');
            const newScopes = currentScopes.filter((s) => s !== oldClaim);
            if (value && !newScopes.includes(value)) {
              newScopes.push(value);
            }
            updateValue(selectedTab, 'OIDC_SCOPES', newScopes);
          }
        }
      }
    },
    [selectedTab, updateValue, values],
  );

  // Memoize hasChanges to avoid expensive JSON.stringify comparisons on every render
  // Must be before early returns to satisfy React's rules of hooks
  const currentTabHasChanges = useMemo(
    () => (selectedTab ? hasChanges(selectedTab) : false),
    [selectedTab, hasChanges],
  );

  if (!isOpen && !isClosing) return null;

  const currentTab = tabs.find((t) => t.name === selectedTab);
  const currentTabDisplayName = currentTab?.displayName || 'Settings';
  const selectedAuthMethod = values.security?.AUTH_METHOD;
  const usersAuthMode = typeof selectedAuthMethod === 'string' ? selectedAuthMethod : authMode;
  const currentTabContent = currentTab ? (
    selectedTab === 'security' && securityAccessError ? (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8">
        <p className="text-sm opacity-60">{securityAccessError}</p>
      </div>
    ) : (
      <SettingsContent
        tab={currentTab}
        values={values[currentTab.name] || {}}
        onChange={handleFieldChange}
        onSave={handleSave}
        onAction={handleAction}
        isSaving={isSaving}
        hasChanges={currentTabHasChanges}
        isUniversalMode={isUniversalMode}
        overrideSummary={tabOverrideSummaries[currentTab.name]}
        customFieldContext={{
          authMode: usersAuthMode,
          onShowToast,
          onRefreshOverrideSummary: handleRefreshCurrentTabOverrideSummary,
          onRefreshAuth,
          onSettingsSaved,
        }}
      />
    )
  ) : null;

  // Loading state
  if (isLoading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <button
          type="button"
          className="absolute inset-0 bg-black/50 backdrop-blur-[2px]"
          style={{ willChange: 'opacity', contain: 'strict' }}
          onClick={handleClose}
          tabIndex={-1}
          aria-label="Close settings"
        />
        <div
          className="relative rounded-xl bg-(--bg) p-8 shadow-2xl"
          style={{ background: 'var(--bg)' }}
        >
          <div className="flex items-center gap-3">
            <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24">
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
                fill="none"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
            <span>Loading settings...</span>
          </div>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <button
          type="button"
          className="absolute inset-0 bg-black/50 backdrop-blur-[2px]"
          style={{ willChange: 'opacity', contain: 'strict' }}
          onClick={handleClose}
          tabIndex={-1}
          aria-label="Close settings"
        />
        <div
          className="relative max-w-md rounded-xl bg-(--bg) p-8 shadow-2xl"
          style={{ background: 'var(--bg)' }}
        >
          <div className="space-y-4 text-center">
            <div className="text-red-500">
              <svg
                className="mx-auto h-12 w-12"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
                />
              </svg>
            </div>
            <p className="text-sm">{error}</p>
            <button
              onClick={handleClose}
              className="rounded-lg border border-(--border-muted) bg-(--bg-soft) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface)"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Mobile layout
  if (isMobile) {
    return (
      <div
        className={`fixed inset-0 z-50 flex flex-col ${isClosing ? 'animate-fade-out' : 'animate-fade-in'}`}
        style={{ background: 'var(--bg)' }}
      >
        {!showMobileDetail ? (
          // Category list view
          <>
            <SettingsHeader title="Settings" onClose={handleClose} />
            <SettingsSidebar
              tabs={tabs}
              groups={groups}
              selectedTab={selectedTab}
              onSelectTab={handleSelectTab}
              mode="list"
            />
          </>
        ) : (
          // Detail view
          <>
            <SettingsHeader
              title={currentTabDisplayName}
              showBack
              onBack={handleBack}
              onClose={handleClose}
            />
            {currentTabContent}
          </>
        )}
      </div>
    );
  }

  // Desktop layout
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <button
        type="button"
        className={`absolute inset-0 bg-black/50 backdrop-blur-[2px] transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        style={{ willChange: 'opacity', contain: 'strict' }}
        onClick={handleClose}
        tabIndex={-1}
        aria-label="Close settings"
      />

      {/* Modal */}
      <div
        className={`relative flex h-[85vh] max-h-[750px] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-(--border-muted) shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
      >
        <SettingsHeader title="Settings" onClose={handleClose} />

        <div className="flex min-h-0 flex-1">
          <SettingsSidebar
            tabs={tabs}
            groups={groups}
            selectedTab={selectedTab}
            onSelectTab={handleSelectTab}
            mode="sidebar"
          />

          {currentTabContent ?? (
            <div className="flex flex-1 items-center justify-center text-sm opacity-60">
              Select a category to configure
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
