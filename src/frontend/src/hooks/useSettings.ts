import { useState, useCallback, useEffect, useRef } from 'react';
import { getSettings, updateSettings, executeSettingsAction } from '../services/api';
import {
  SettingsTab,
  SettingsGroup,
  SettingsField,
  ActionResult,
  UpdateResult,
} from '../types/settings';
import {
  getStoredThemePreference,
  setThemePreference,
  THEME_FIELD,
} from '../utils/themePreference';
import {
  cloneSettingsValues,
  mergeFetchedSettingsWithDirtyValues,
  type SettingsValues,
} from '../utils/settingsValues';

type ValueBearingField = Exclude<
  SettingsField,
  { type: 'ActionButton' } | { type: 'HeadingField' } | { type: 'CustomComponentField' }
>;

interface FetchSettingsOptions {
  silent?: boolean;
  preserveDirtyValues?: boolean;
}

// Extract value from a field based on its type
function getFieldValue(field: SettingsField): unknown {
  // These field types have no value property
  if (
    field.type === 'ActionButton'
    || field.type === 'HeadingField'
    || field.type === 'CustomComponentField'
  ) {
    return undefined;
  }

  if (field.type === 'TableField') {
    return (field as unknown as { value?: unknown }).value ?? [];
  }

  // All other fields have a value property
  return field.value ?? '';
}

function getValueBearingFields(fields: SettingsField[]): ValueBearingField[] {
  const seen = new Set<string>();
  const valueFields: ValueBearingField[] = [];

  const collect = (items: SettingsField[]) => {
    items.forEach((field) => {
      if (field.type === 'CustomComponentField') {
        if (field.boundFields && field.boundFields.length > 0) {
          collect(field.boundFields);
        }
        return;
      }

      if (field.type === 'ActionButton' || field.type === 'HeadingField') {
        return;
      }

      if (seen.has(field.key)) {
        return;
      }
      seen.add(field.key);
      valueFields.push(field);
    });
  };

  collect(fields);
  return valueFields;
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

  useEffect(() => {
    valuesRef.current = values;
  }, [values]);

  useEffect(() => {
    originalValuesRef.current = originalValues;
  }, [originalValues]);

  const fetchSettings = useCallback(async (options: FetchSettingsOptions = {}) => {
    const { silent = false, preserveDirtyValues = false } = options;
    if (!silent) {
      setIsLoading(true);
    }
    setError(null);
    try {
      const response = await getSettings();

      // Inject theme field into the general tab at the beginning
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

      // Initialize values from fetched data
      const initialValues: SettingsValues = {};
      tabsWithTheme.forEach((tab) => {
        initialValues[tab.name] = {};
        getValueBearingFields(tab.fields).forEach((field) => {
          // Special handling for theme field - get from localStorage
          if (field.key === '_THEME') {
            initialValues[tab.name][field.key] = getStoredThemePreference();
          } else {
            initialValues[tab.name][field.key] = getFieldValue(field);
          }
        });
      });

      const nextValues = preserveDirtyValues
        ? mergeFetchedSettingsWithDirtyValues(initialValues, valuesRef.current, originalValuesRef.current)
        : initialValues;

      setValues(nextValues);
      setOriginalValues(cloneSettingsValues(initialValues));

      // Select first tab by default if none selected
      if (tabsWithTheme.length > 0) {
        setSelectedTab((current) => current ?? tabsWithTheme[0].name);
      }
    } catch (err) {
      console.error('Failed to fetch settings:', err);
      setError(err instanceof Error ? err.message : 'Failed to load settings');
    } finally {
      if (!silent) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  const updateValue = useCallback((tabName: string, key: string, value: unknown) => {
    // Apply theme immediately when changed (no save button needed)
    if (key === '_THEME' && typeof value === 'string') {
      setThemePreference(value);
      // Also update original value so it doesn't show as pending change
      setOriginalValues((prev) => ({
        ...prev,
        [tabName]: {
          ...prev[tabName],
          [key]: value,
        },
      }));
    }

    setValues((prev) => ({
      ...prev,
      [tabName]: {
        ...prev[tabName],
        [key]: value,
      },
    }));
  }, []);

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
    [values, originalValues, tabs]
  );

  const saveTab = useCallback(
    async (tabName: string): Promise<UpdateResult> => {
      setIsSaving(true);
      try {
        const tabValues = values[tabName] || {};
        const originalTabValues = originalValues[tabName] || {};

        // Only send values that actually changed
        const tab = tabs.find((t) => t.name === tabName);
        const valuesToSave: Record<string, unknown> = {};

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
        }

        const result = await updateSettings(tabName, valuesToSave);

        if (result.success) {
          // Refetch all settings silently to pick up any backend-triggered changes
          // (e.g., enabling a metadata provider auto-updates METADATA_PROVIDER)
          await fetchSettings({ silent: true });
        }

        return result;
      } catch (err) {
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
    [values, originalValues, tabs]
  );

  const executeAction = useCallback(
    async (tabName: string, actionKey: string): Promise<ActionResult> => {
      try {
        // Pass current form values so action can use unsaved values
        const currentValues = values[tabName] || {};
        const result = await executeSettingsAction(tabName, actionKey, currentValues);

        // Re-fetch settings after successful action to pick up updated options
        // (e.g., BookLore "Test Connection" refreshes library/path lists)
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
    [values, fetchSettings]
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
