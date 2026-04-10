import { SettingsField, SettingsTab } from '../types/settings';

export type SettingsValues = Record<string, Record<string, unknown>>;

type ValueBearingField = Exclude<
  SettingsField,
  { type: 'ActionButton' } | { type: 'HeadingField' } | { type: 'CustomComponentField' }
>;

export function getFieldValue(field: SettingsField): unknown {
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

  return field.value ?? '';
}

export function getValueBearingFields(fields: SettingsField[]): ValueBearingField[] {
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

export function extractSettingsValues(tabs: SettingsTab[]): SettingsValues {
  const values: SettingsValues = {};

  tabs.forEach((tab) => {
    values[tab.name] = {};
    getValueBearingFields(tab.fields).forEach((field) => {
      values[tab.name][field.key] = getFieldValue(field);
    });
  });

  return values;
}

export function getRestartRequiredFieldKeys(
  fields: SettingsField[],
  changedValues: Record<string, unknown>
): string[] {
  return getValueBearingFields(fields)
    .filter((field) => field.requiresRestart && Object.prototype.hasOwnProperty.call(changedValues, field.key))
    .map((field) => field.key);
}

export function settingsTabMatchesSavedValues(
  tabName: string,
  tabs: SettingsTab[],
  expectedValues: Record<string, unknown>
): boolean {
  const tab = tabs.find((entry) => entry.name === tabName);
  if (!tab) {
    return false;
  }

  let verifiedFieldCount = 0;

  for (const field of getValueBearingFields(tab.fields)) {
    if (!Object.prototype.hasOwnProperty.call(expectedValues, field.key)) {
      continue;
    }

    // Passwords are intentionally not returned by the backend, so they
    // cannot be verified from a follow-up fetch.
    if (field.type === 'PasswordField') {
      continue;
    }

    verifiedFieldCount += 1;
    if (JSON.stringify(getFieldValue(field)) !== JSON.stringify(expectedValues[field.key])) {
      return false;
    }
  }

  return verifiedFieldCount > 0;
}

export function cloneSettingsValues(values: SettingsValues): SettingsValues {
  return JSON.parse(JSON.stringify(values)) as SettingsValues;
}

export function mergeFetchedSettingsWithDirtyValues(
  fetchedValues: SettingsValues,
  currentValues: SettingsValues,
  originalValues: SettingsValues
): SettingsValues {
  const mergedValues: SettingsValues = {};

  for (const [tabName, fetchedTabValues] of Object.entries(fetchedValues)) {
    mergedValues[tabName] = { ...fetchedTabValues };
    const currentTabValues = currentValues[tabName] ?? {};
    const originalTabValues = originalValues[tabName] ?? {};

    for (const [key, currentValue] of Object.entries(currentTabValues)) {
      if (!(key in fetchedTabValues)) {
        continue;
      }

      const originalValue = originalTabValues[key];
      if (JSON.stringify(currentValue) !== JSON.stringify(originalValue)) {
        mergedValues[tabName][key] = currentValue;
      }
    }
  }

  return mergedValues;
}
