export type SettingsValues = Record<string, Record<string, unknown>>;

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
