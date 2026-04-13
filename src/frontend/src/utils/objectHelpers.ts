export const isRecord = (value: unknown): value is Record<string, unknown> => {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
};

export const isStringArray = (value: unknown): value is string[] => {
  return Array.isArray(value) && value.every((entry) => typeof entry === 'string');
};

export const toStringValue = (value: unknown): string | undefined => {
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false';
  }
  return undefined;
};

export const toNumberValue = (value: unknown): number | undefined => {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
};

export const toBooleanValue = (value: unknown): boolean | undefined => {
  return typeof value === 'boolean' ? value : undefined;
};

export const toStringArray = (value: unknown): string[] | undefined => {
  return isStringArray(value) ? value : undefined;
};

export const toComparableText = (value: unknown): string => {
  if (value === null || value === undefined) {
    return '';
  }

  const text = toStringValue(value);
  if (text !== undefined) {
    return text;
  }

  if (Array.isArray(value)) {
    return value
      .map((entry) => toComparableText(entry))
      .filter(Boolean)
      .join(', ');
  }

  if (isRecord(value)) {
    try {
      return JSON.stringify(value) ?? '';
    } catch {
      return '';
    }
  }

  return '';
};

/**
 * Get a nested value from an object using dot-notation path.
 * e.g., getNestedValue(obj, "extra.language") returns obj.extra.language
 */
export function getNestedValue(obj: unknown, path: string): unknown {
  return path.split('.').reduce((current, key) => {
    if (isRecord(current)) {
      return current[key];
    }
    return undefined;
  }, obj);
}
