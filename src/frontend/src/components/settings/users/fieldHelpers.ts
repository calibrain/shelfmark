import { SettingsField } from '../../../types/settings';

export { isRecord } from '../../../utils/objectHelpers';

const hasMatchingFieldType = <T extends SettingsField>(
  field: SettingsField,
  fallback: T,
): field is T => field.type === fallback.type;

const isPrimitiveTextValue = (value: unknown): value is string | number | boolean | bigint => {
  return (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean' ||
    typeof value === 'bigint'
  );
};

export const toTextValue = (value: unknown): string => {
  if (value === null || value === undefined) {
    return '';
  }
  return isPrimitiveTextValue(value) ? String(value) : '';
};

export const toTrimmedTextValue = (value: unknown): string => {
  return toTextValue(value).trim();
};

export const toNormalizedLowercaseTextValue = (value: unknown): string => {
  return toTrimmedTextValue(value).toLowerCase();
};

export const toComparableValue = (value: unknown): string => {
  if (value === null || value === undefined) {
    return '';
  }
  if (isPrimitiveTextValue(value)) {
    return String(value);
  }
  if (typeof value !== 'object') {
    return '';
  }

  try {
    return JSON.stringify(value) ?? '';
  } catch {
    return '';
  }
};

export const getFieldByKey = <T extends SettingsField>(
  fields: SettingsField[] | undefined,
  key: string,
  fallback: T,
): T => {
  const found = fields?.find((field) => field.key === key);
  if (found && hasMatchingFieldType(found, fallback)) {
    return found;
  }
  return fallback;
};
