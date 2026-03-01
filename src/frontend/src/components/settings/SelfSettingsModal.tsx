import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AdminUser,
  DeliveryPreferencesResponse,
  getSelfUserEditContext,
  testSelfNotificationPreferences,
  updateSelfUser,
} from '../../services/api';
import { SelectField } from './fields';
import { FieldWrapper } from './shared';
import { UserAccountCardContent, UserEditActions, UserIdentityHeader } from './users/UserCard';
import {
  DEFAULT_SELF_USER_OVERRIDE_SECTIONS,
  normalizeUserOverrideSections,
  UserOverridesSections,
  type UserOverrideSectionId,
} from './users';
import { PerUserSettings } from './users/types';
import { useUserOverridesState } from './users/useUserOverridesState';
import { getStoredThemePreference, setThemePreference, THEME_FIELD } from '../../utils/themePreference';

interface SelfSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved?: () => void;
}

const MIN_PASSWORD_LENGTH = 4;

const getPasswordError = (password: string, passwordConfirm: string): string | null => {
  if (!password && !passwordConfirm) {
    return null;
  }
  if (!password) {
    return 'Password is required';
  }
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters`;
  }
  return password === passwordConfirm ? null : 'Passwords do not match';
};

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

export const SelfSettingsModal = ({
  isOpen,
  onClose,
  onShowToast,
  onSettingsSaved,
}: SelfSettingsModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [originalUser, setOriginalUser] = useState<AdminUser | null>(null);
  const [deliveryPreferences, setDeliveryPreferences] = useState<DeliveryPreferencesResponse | null>(null);
  const [searchPreferences, setSearchPreferences] = useState<DeliveryPreferencesResponse | null>(null);
  const [notificationPreferences, setNotificationPreferences] = useState<DeliveryPreferencesResponse | null>(null);
  const [visibleSections, setVisibleSections] = useState<UserOverrideSectionId[]>(
    DEFAULT_SELF_USER_OVERRIDE_SECTIONS
  );

  const [editPassword, setEditPassword] = useState('');
  const [editPasswordConfirm, setEditPasswordConfirm] = useState('');

  const [themeValue, setThemeValue] = useState<string>(getStoredThemePreference());

  const preferenceGroups = useMemo(
    () => [deliveryPreferences, searchPreferences, notificationPreferences],
    [deliveryPreferences, searchPreferences, notificationPreferences]
  );
  const {
    userSettings,
    setUserSettings,
    isUserOverridable,
    currentSettingsPayload,
    hasUserSettingsChanges: hasSettingsChanges,
    applyUserOverridesContext,
  } = useUserOverridesState({ preferenceGroups });

  const loadEditContext = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const context = await getSelfUserEditContext();

      setEditingUser(context.user);
      setOriginalUser(context.user);
      setDeliveryPreferences(context.deliveryPreferences || null);
      setSearchPreferences(context.searchPreferences || null);
      setNotificationPreferences(context.notificationPreferences || null);
      setVisibleSections(
        normalizeUserOverrideSections(context.visibleUserSettingsSections, 'self')
      );
      applyUserOverridesContext({
        settings: (context.user.settings || {}) as PerUserSettings,
        userOverridableKeys: context.userOverridableKeys || [],
      });
      setEditPassword('');
      setEditPasswordConfirm('');
    } catch (error) {
      setLoadError(getErrorMessage(error, 'Failed to load account settings'));
    } finally {
      setIsLoading(false);
    }
  }, [applyUserOverridesContext]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setIsClosing(false);
    void loadEditContext();
  }, [isOpen, loadEditContext]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

  const handleClose = useCallback(() => {
    if (isSaving) {
      return;
    }
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [isSaving, onClose]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleClose();
      }
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, handleClose]);

  const hasProfileChanges = Boolean(
    editingUser
    && originalUser
    && (
      editingUser.email !== originalUser.email
      || editingUser.display_name !== originalUser.display_name
    )
  );

  const hasPasswordChanges = editPassword.length > 0 || editPasswordConfirm.length > 0;
  const passwordError = getPasswordError(editPassword, editPasswordConfirm);
  const hasChanges = hasSettingsChanges || hasProfileChanges || hasPasswordChanges;

  const handleTestNotificationRoutes = useCallback((routes: Array<Record<string, unknown>>) => {
    return testSelfNotificationPreferences(routes);
  }, []);

  const handleSave = useCallback(async () => {
    if (!editingUser || !originalUser) {
      return;
    }
    if (passwordError) {
      onShowToast?.(passwordError, 'error');
      return;
    }

    const payload: {
      email?: string | null;
      display_name?: string | null;
      password?: string;
      settings?: Record<string, unknown>;
    } = {};

    if (
      editingUser.edit_capabilities.canEditEmail
      && editingUser.email !== originalUser.email
    ) {
      payload.email = editingUser.email;
    }
    if (
      editingUser.edit_capabilities.canEditDisplayName
      && editingUser.display_name !== originalUser.display_name
    ) {
      payload.display_name = editingUser.display_name;
    }
    if (editingUser.edit_capabilities.canSetPassword && editPassword) {
      payload.password = editPassword;
    }
    if (hasSettingsChanges) {
      payload.settings = currentSettingsPayload;
    }

    if (Object.keys(payload).length === 0) {
      return;
    }

    setIsSaving(true);
    try {
      await updateSelfUser(payload);
      onShowToast?.('Account updated', 'success');
      onSettingsSaved?.();
      await loadEditContext();
    } catch (error) {
      onShowToast?.(getErrorMessage(error, 'Failed to update account'), 'error');
    } finally {
      setIsSaving(false);
    }
  }, [
    currentSettingsPayload,
    editingUser,
    hasSettingsChanges,
    loadEditContext,
    onSettingsSaved,
    onShowToast,
    originalUser,
    passwordError,
    editPassword,
  ]);

  if (!isOpen && !isClosing) {
    return null;
  }

  const titleId = 'self-settings-modal-title';
  const hasCachedEditContext = Boolean(editingUser);
  const showInitialLoadingState = isLoading && !hasCachedEditContext;
  const showInitialLoadErrorState = Boolean(loadError) && !hasCachedEditContext;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className={`absolute inset-0 bg-black/50 backdrop-blur-sm transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        onClick={handleClose}
      />

      <div
        className={`relative w-full max-w-3xl h-[85vh] max-h-[750px] rounded-xl border border-[var(--border-muted)] shadow-2xl flex flex-col overflow-hidden ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="flex items-center justify-between border-b border-[var(--border-muted)] px-6 py-4">
          <h3 id={titleId} className="sr-only">My Account</h3>
          {editingUser ? (
            <UserIdentityHeader
              user={editingUser}
              showAuthSource
              showInactiveState={false}
            />
          ) : (
            <div className="text-sm font-medium">My Account</div>
          )}
          <div className="flex items-center">
            <button
              type="button"
              onClick={handleClose}
              className="p-2 rounded-full hover-action transition-colors text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Close account settings"
              disabled={isSaving}
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {showInitialLoadingState ? (
            <div className="h-full flex items-center justify-center text-sm opacity-60">
              Loading account settings...
            </div>
          ) : showInitialLoadErrorState ? (
            <div className="h-full flex flex-col items-center justify-center gap-3 text-center">
              <p className="text-sm opacity-70">{loadError}</p>
              <button
                type="button"
                onClick={() => { void loadEditContext(); }}
                className="px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)] bg-[var(--bg-soft)] hover:bg-[var(--hover-surface)] transition-colors"
              >
                Retry
              </button>
            </div>
          ) : editingUser ? (
            <div className="space-y-5">
              <FieldWrapper field={THEME_FIELD}>
                <SelectField
                  field={THEME_FIELD}
                  value={themeValue}
                  onChange={(value) => {
                    setThemeValue(value);
                    setThemePreference(value);
                  }}
                />
              </FieldWrapper>

              <UserAccountCardContent
                user={editingUser}
                onUserChange={setEditingUser}
                onSave={() => {}}
                saving={isSaving}
                onCancel={handleClose}
                hideEditActions
                editPassword={editPassword}
                onEditPasswordChange={setEditPassword}
                editPasswordConfirm={editPasswordConfirm}
                onEditPasswordConfirmChange={setEditPasswordConfirm}
                preferencesPlacement="after"
                preferencesPanel={{
                  hideTitle: true,
                  children: (
                    <UserOverridesSections
                      scope="self"
                      sections={visibleSections}
                      deliveryPreferences={deliveryPreferences}
                      searchPreferences={searchPreferences}
                      notificationPreferences={notificationPreferences}
                      isUserOverridable={isUserOverridable}
                      userSettings={userSettings}
                      setUserSettings={setUserSettings}
                      onTestNotificationRoutes={handleTestNotificationRoutes}
                    />
                  ),
                }}
              />
            </div>
          ) : (
            <div className="h-full flex items-center justify-center text-sm opacity-60">
              Unable to load account details.
            </div>
          )}
        </div>

        <footer className="flex items-center justify-end gap-3 border-t border-[var(--border-muted)] px-6 py-4">
          <UserEditActions
            variant="modalFooter"
            onSave={() => {
              void handleSave();
            }}
            saving={isSaving}
            saveDisabled={!hasChanges || isSaving || isLoading}
            onCancel={handleClose}
            cancelDisabled={isSaving}
          />
        </footer>
      </div>
    </div>
  );
};
