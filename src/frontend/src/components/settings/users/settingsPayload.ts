import { DeliveryPreferencesResponse } from '../../../services/api';
import { PerUserSettings } from './types';

const normalizeComparableValue = (value: unknown): string => {
  if (value === null || value === undefined) {
    return '';
  }
  return String(value);
};

export const buildUserSettingsPayload = (
  userSettings: PerUserSettings,
  userOverridableSettings: Set<string>,
  deliveryPreferences: DeliveryPreferencesResponse | null,
): Record<string, unknown> =>
  (deliveryPreferences?.keys || [...userOverridableSettings])
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
      const globalValue = deliveryPreferences?.globalValues?.[key];
      const isDifferentFromGlobal = deliveryPreferences
        ? normalizeComparableValue(userValue) !== normalizeComparableValue(globalValue)
        : true;

      payload[key] = isDifferentFromGlobal ? userValue : null;
      return payload;
    }, {});
