import { DeliveryPreferencesResponse } from '../../../services/api';
import { PerUserSettings } from './types';

const normalizeComparableValue = (value: unknown): string => {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
};

export const buildUserSettingsPayload = (
  userSettings: PerUserSettings,
  userOverridableSettings: Set<string>,
  preferenceGroups: Array<DeliveryPreferencesResponse | null>,
): Record<string, unknown> =>
  Array.from(new Set([
    ...preferenceGroups.flatMap((preferences) => preferences?.keys || []),
    ...userOverridableSettings,
  ]))
    .map(String)
    .sort()
    .reduce<Record<string, unknown>>((payload, key) => {
      const typedKey = key as keyof PerUserSettings;
      const hasUserValue = Object.prototype.hasOwnProperty.call(userSettings, typedKey)
        && userSettings[typedKey] !== null
        && userSettings[typedKey] !== undefined;

      if (!hasUserValue) {
        payload[key] = null;
        return payload;
      }

      const userValue = userSettings[typedKey];
      const matchingPreferences = preferenceGroups.find((preferences) =>
        preferences?.keys?.includes(key)
      );
      const hasGlobalValue = Boolean(
        matchingPreferences
        && Object.prototype.hasOwnProperty.call(matchingPreferences.globalValues, key)
      );
      const globalValue = matchingPreferences?.globalValues?.[key];
      const isDifferentFromGlobal = hasGlobalValue
        ? normalizeComparableValue(userValue) !== normalizeComparableValue(globalValue)
        : true;

      payload[key] = isDifferentFromGlobal ? userValue : null;
      return payload;
    }, {});
