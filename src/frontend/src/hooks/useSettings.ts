import { useState, useCallback, useEffect, useRef } from 'react';

import { getSettings, updateSettings, executeSettingsAction } from '../services/api';
import type {
  SettingsResponse,
  SettingsTab,
  SettingsGroup,
  ActionResult,
  UpdateResult,
} from '../types/settings';
import {
  cloneSettingsValues,
  extractSettingsValues,
  getRestartRequiredFieldKeys,
  getValueBearingFields,
  mergeFetchedSettingsWithDirtyValues,
  normalizeDependentSelectValues,
  settingsTabMatchesSavedValues,
  type SettingsValues,
} from '../utils/settingsValues';
import {
  getStoredThemePreference,
  setThemePreference,
  THEME_FIELD,
} from '../utils/themePreference';

interface FetchSettingsOptions {
  silent?: boolean;
  preserveDirtyValues?: boolean;
}

interface UseSettingsReturn {
  tabs: SettingsTab[];
  groups: SettingsGroup[];
  isLoading: boolean;
  error: string | null;
  selectedTab: string | null;
  setSelectedTab: (tab: string | null) => void;
  values: Record<string, Record<string, unknown>>;
  updateValue: (tabName: string, key: string, value: unknown) => void;
  hasChanges: (tabName: string) => boolean;
  saveTab: (tabName: string) => Promise<UpdateResult>;
  executeAction: (tabName: string, actionKey: string) => Promise<ActionResult>;
  isSaving: boolean;
  refetch: () => Promise<void>;
}

export function useSettings(): UseSettingsReturn {
  const [tabs, setTabs] = useState<SettingsTab[]>([]);
  const [groups, setGroups] = useState<SettingsGroup[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [values, setValues] = useState<SettingsValues>({});
  const [originalValues, setOriginalValues] = useState<SettingsValues>({});
  const [isSaving, setIsSaving] = useState(false);
  const valuesRef = useRef<SettingsValues>({});
  const originalValuesRef = useRef<SettingsValues>({});

  valuesRef.current = values;
  originalValuesRef.current = originalValues;

  const applySettingsResponse = useCallback(
    (response: SettingsResponse, options: { preserveDirtyValues?: boolean } = {}) => {
      const { preserveDirtyValues = false } = options;

      const tabsWithTheme = response.tabs.map((tab) => {
        if (tab.name === 'general') {
          return {
            ...tab,
            fields: [THEME_FIELD, ...tab.fields],
          };
        }
        return tab;
      });

      setTabs(tabsWithTheme);
      setGroups(response.groups || []);

      const initialValues = extractSettingsValues(tabsWithTheme);
      if (
        initialValues.general &&
        Object.prototype.hasOwnProperty.call(initialValues.general, '_THEME')
      ) {
        initialValues.general._THEME = getStoredThemePreference();
      }

      const nextValues = preserveDirtyValues
        ? mergeFetchedSettingsWithDirtyValues(
            initialValues,
            valuesRef.current,
            originalValuesRef.current,
          )
        : initialValues;

      setValues(nextValues);
      setOriginalValues(cloneSettingsValues(initialValues));

      if (tabsWithTheme.length > 0) {
        setSelectedTab((current) => current ?? tabsWithTheme[0].name);
      }
    },
    [],
  );

  const fetchSettings = useCallback(
    async (options: FetchSettingsOptions = {}) => {
      const { silent = false, preserveDirtyValues = false } = options;
      if (!silent) {
        setIsLoading(true);
        setError(null);
      }
      try {
        const response = await getSettings();
        applySettingsResponse(response, { preserveDirtyValues });
      } catch (err) {
        console.error('Failed to fetch settings:', err);
        if (!silent) {
          setError(err instanceof Error ? err.message : 'Failed to load settings');
        }
      } finally {
        if (!silent) {
          setIsLoading(false);
        }
      }
    },
    [applySettingsResponse],
  );

  useEffect(() => {
    void fetchSettings();
  }, [fetchSettings]);

  const updateValue = useCallback(
    (tabName: string, key: string, value: unknown) => {
      if (key === '_THEME' && typeof value === 'string') {
        setThemePreference(value);
        setOriginalValues((prev) => ({
          ...prev,
          [tabName]: {
            ...prev[tabName],
            [key]: value,
          },
        }));
      }

      setValues((prev) => {
        const tab = tabs.find((entry) => entry.name === tabName);
        const nextTabValues = {
          ...prev[tabName],
          [key]: value,
        };

        return {
          ...prev,
          [tabName]: tab
            ? normalizeDependentSelectValues(tab.fields, nextTabValues)
            : nextTabValues,
        };
      });
    },
    [tabs],
  );

  const hasChanges = useCallback(
    (tabName: string) => {
      const current = values[tabName];
      const original = originalValues[tabName];
      if (!current || !original) return false;

      const tab = tabs.find((t) => t.name === tabName);
      if (!tab) return false;

      for (const field of getValueBearingFields(tab.fields)) {
        const currentValue = current[field.key];
        const originalValue = original[field.key];

        // Compare values - works for all field types including password
        if (JSON.stringify(currentValue) !== JSON.stringify(originalValue)) {
          return true;
        }
      }

      return false;
    },
    [values, originalValues, tabs],
  );

  const saveTab = useCallback(
    async (tabName: string): Promise<UpdateResult> => {
      setIsSaving(true);
      let valuesToSave: Record<string, unknown> = {};
      let restartRequiredFor: string[] = [];
      try {
        const tabValues = values[tabName] || {};
        const originalTabValues = originalValues[tabName] || {};

        // Only send values that actually changed
        const tab = tabs.find((t) => t.name === tabName);
        valuesToSave = {};

        if (tab) {
          for (const field of getValueBearingFields(tab.fields)) {
            if (field.fromEnv) continue; // Skip env-locked fields
            if (field.key === '_THEME') continue; // Skip client-side only theme field

            const value = tabValues[field.key];
            const originalValue = originalTabValues[field.key];

            // Skip empty password fields
            if (field.type === 'PasswordField' && (!value || value === '')) {
              continue;
            }

            // Only include if value actually changed
            if (JSON.stringify(value) !== JSON.stringify(originalValue)) {
              valuesToSave[field.key] = value;
            }
          }

          restartRequiredFor = getRestartRequiredFieldKeys(tab.fields, valuesToSave);
        }

        const result = await updateSettings(tabName, valuesToSave);

        if (result.success) {
          // Refetch all settings silently to pick up any backend-triggered changes
          // (e.g., enabling a metadata provider auto-updates METADATA_PROVIDER)
          await fetchSettings({ silent: true });
        }

        return result;
      } catch (err) {
        if (Object.keys(valuesToSave).length > 0) {
          try {
            const response = await getSettings();
            if (settingsTabMatchesSavedValues(tabName, response.tabs, valuesToSave)) {
              setError(null);
              applySettingsResponse(response);
              return {
                success: true,
                message:
                  'Settings saved, but the proxy interrupted the response. Latest values were confirmed.',
                updated: Object.keys(valuesToSave),
                requiresRestart: restartRequiredFor.length > 0,
                restartRequiredFor,
              };
            }
          } catch (recoveryError) {
            console.warn('Failed to verify settings save after response error:', recoveryError);
          }
        }

        console.error('Failed to save settings tab:', tabName, err);
        return {
          success: false,
          message: err instanceof Error ? err.message : 'Failed to save settings',
          updated: [],
        };
      } finally {
        setIsSaving(false);
      }
    },
    [applySettingsResponse, fetchSettings, originalValues, tabs, values],
  );

  const executeAction = useCallback(
    async (tabName: string, actionKey: string): Promise<ActionResult> => {
      try {
        // Pass current form values so action can use unsaved values
        const currentValues = values[tabName] || {};
        const result = await executeSettingsAction(tabName, actionKey, currentValues);

        // Re-fetch settings after successful action to pick up updated options
        // (e.g., Grimmory "Test Connection" refreshes library/path lists)
        if (result.success) {
          await fetchSettings({ silent: true, preserveDirtyValues: true });
        }

        return result;
      } catch (err) {
        console.error('Action execution failed:', tabName, actionKey, err);
        return {
          success: false,
          message: err instanceof Error ? err.message : 'Action failed',
        };
      }
    },
    [values, fetchSettings],
  );

  return {
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
    refetch: () => fetchSettings(),
  };
}
