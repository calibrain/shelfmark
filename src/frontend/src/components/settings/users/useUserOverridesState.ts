import { useCallback, useMemo, useState } from 'react';
import { DeliveryPreferencesResponse } from '../../../services/api';
import { buildUserSettingsPayload } from './settingsPayload';
import { PerUserSettings } from './types';

interface UseUserOverridesStateParams {
  preferenceGroups: Array<DeliveryPreferencesResponse | null>;
}

interface ApplyUserOverridesContextParams {
  settings: PerUserSettings;
  userOverridableKeys: Iterable<string>;
}

const normalizeUserSettings = (settings: PerUserSettings): PerUserSettings => {
  const normalized: PerUserSettings = {};
  Object.keys(settings).sort().forEach((key) => {
    const typedKey = key as keyof PerUserSettings;
    const value = settings[typedKey];
    if (value !== null && value !== undefined) {
      normalized[typedKey] = value;
    }
  });
  return normalized;
};

export const useUserOverridesState = ({
  preferenceGroups,
}: UseUserOverridesStateParams) => {
  const [userSettings, setUserSettings] = useState<PerUserSettings>({});
  const [originalUserSettings, setOriginalUserSettings] = useState<PerUserSettings>({});
  const [userOverridableSettings, setUserOverridableSettings] = useState<Set<string>>(new Set());

  const applyUserOverridesContext = useCallback(({
    settings,
    userOverridableKeys,
  }: ApplyUserOverridesContextParams) => {
    const normalizedSettings = normalizeUserSettings(settings);
    setUserSettings(normalizedSettings);
    setOriginalUserSettings(normalizedSettings);
    setUserOverridableSettings(new Set(userOverridableKeys));
  }, []);

  const resetUserOverridesState = useCallback(() => {
    setUserSettings({});
    setOriginalUserSettings({});
    setUserOverridableSettings(new Set());
  }, []);

  const isUserOverridable = useCallback(
    (key: keyof PerUserSettings) => userOverridableSettings.has(String(key)),
    [userOverridableSettings]
  );

  const currentSettingsPayload = useMemo(
    () => buildUserSettingsPayload(
      userSettings,
      userOverridableSettings,
      preferenceGroups
    ),
    [preferenceGroups, userOverridableSettings, userSettings]
  );

  const originalSettingsPayload = useMemo(
    () => buildUserSettingsPayload(
      originalUserSettings,
      userOverridableSettings,
      preferenceGroups
    ),
    [originalUserSettings, preferenceGroups, userOverridableSettings]
  );

  const hasUserSettingsChanges =
    JSON.stringify(currentSettingsPayload) !== JSON.stringify(originalSettingsPayload);

  return {
    userSettings,
    setUserSettings,
    userOverridableSettings,
    setUserOverridableSettings,
    isUserOverridable,
    currentSettingsPayload,
    hasUserSettingsChanges,
    applyUserOverridesContext,
    resetUserOverridesState,
  };
};
