import { DeliveryPreferencesResponse } from '../../../services/api';
import { toComparableValue } from './fieldHelpers';
import { PerUserSettings } from './types';

const normalizeComparableValue = (value: unknown): string => {
  return toComparableValue(value);
};

export const buildUserSettingsPayload = (
  userSettings: PerUserSettings,
  userOverridableSettings: Set<string>,
  preferenceGroups: Array<DeliveryPreferencesResponse | null>,
): Record<string, unknown> => {
  const settingKeys = Array.from(
    new Set([
      ...preferenceGroups.flatMap((preferences) => preferences?.keys || []),
      ...userOverridableSettings,
    ]),
    String,
  ).toSorted();

  return settingKeys.reduce<Record<string, unknown>>((payload, key) => {
    const typedKey = key as keyof PerUserSettings;
    const hasUserValue =
      Object.prototype.hasOwnProperty.call(userSettings, typedKey) &&
      userSettings[typedKey] !== null &&
      userSettings[typedKey] !== undefined;

    if (!hasUserValue) {
      payload[key] = null;
      return payload;
    }

    const userValue = userSettings[typedKey];
    const matchingPreferences = preferenceGroups.find((preferences) =>
      preferences?.keys?.includes(key),
    );
    const hasGlobalValue = Boolean(
      matchingPreferences &&
      Object.prototype.hasOwnProperty.call(matchingPreferences.globalValues, key),
    );
    const globalValue = matchingPreferences?.globalValues?.[key];
    const isDifferentFromGlobal = hasGlobalValue
      ? normalizeComparableValue(userValue) !== normalizeComparableValue(globalValue)
      : true;

    payload[key] = isDifferentFromGlobal ? userValue : null;
    return payload;
  }, {});
};
