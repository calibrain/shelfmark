import type { DeliveryPreferencesResponse } from '../../../services/api';
import { SECRET_PLACEHOLDER } from '../../../utils/settingsValues';
import { toComparableValue } from './fieldHelpers';
import type { PerUserSettings } from './types';

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
    // A masked secret cannot be compared with the global value, which is masked
    // too. Sending the placeholder back tells the backend to keep what is stored.
    if (userValue === SECRET_PLACEHOLDER) {
      payload[key] = userValue;
      return payload;
    }

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
