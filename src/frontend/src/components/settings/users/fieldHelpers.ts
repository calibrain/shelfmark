import { SettingsField } from '../../../types/settings';

export const getFieldByKey = <T extends SettingsField>(
  fields: SettingsField[] | undefined,
  key: string,
  fallback: T
): T => {
  const found = fields?.find((field) => field.key === key);
  if (!found) {
    return fallback;
  }
  return found as T;
};
