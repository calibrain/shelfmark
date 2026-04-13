import type { SelectOption, SettingsField, SettingsTab } from '../types/settings';

export type SettingsValues = Record<string, Record<string, unknown>>;

type ValueBearingField = Exclude<
  SettingsField,
  { type: 'ActionButton' } | { type: 'HeadingField' } | { type: 'CustomComponentField' }
>;

function getFieldValue(field: SettingsField): unknown {
  if (
    field.type === 'ActionButton' ||
    field.type === 'HeadingField' ||
    field.type === 'CustomComponentField'
  ) {
    return undefined;
  }

  if (field.type === 'TableField') {
    return field.value ?? [];
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
  changedValues: Record<string, unknown>,
): string[] {
  return getValueBearingFields(fields)
    .filter(
      (field) =>
        field.requiresRestart && Object.prototype.hasOwnProperty.call(changedValues, field.key),
    )
    .map((field) => field.key);
}

export function settingsTabMatchesSavedValues(
  tabName: string,
  tabs: SettingsTab[],
  expectedValues: Record<string, unknown>,
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
  return structuredClone(values);
}

function toComparableSelectValue(value: unknown): string | undefined {
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return undefined;
}

function getFilteredSelectOptions(
  options: SelectOption[],
  filterValue: string | undefined,
): SelectOption[] {
  if (!filterValue) {
    return options.filter((option) => !option.childOf);
  }

  return options.filter((option) => !option.childOf || option.childOf === filterValue);
}

export function normalizeDependentSelectValues(
  fields: SettingsField[],
  values: Record<string, unknown>,
): Record<string, unknown> {
  const valueFields = getValueBearingFields(fields);
  let nextValues = values;
  let changed = false;

  do {
    changed = false;

    for (const field of valueFields) {
      if (field.type !== 'SelectField' || !field.filterByField) {
        continue;
      }

      const currentValue = toComparableSelectValue(nextValues[field.key]);
      if (!currentValue) {
        continue;
      }

      const filterValue = toComparableSelectValue(nextValues[field.filterByField]);
      const filteredOptions = getFilteredSelectOptions(field.options, filterValue);
      const isCurrentValueValid = filteredOptions.some((option) => option.value === currentValue);

      if (!isCurrentValueValid) {
        if (nextValues === values) {
          nextValues = { ...values };
        }
        nextValues[field.key] = '';
        changed = true;
      }
    }
  } while (changed);

  return nextValues;
}

export function mergeFetchedSettingsWithDirtyValues(
  fetchedValues: SettingsValues,
  currentValues: SettingsValues,
  originalValues: SettingsValues,
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
