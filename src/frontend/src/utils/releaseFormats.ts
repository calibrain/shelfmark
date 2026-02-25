import { Release } from '../types';

function normalizeFormatValue(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  return normalized.length > 0 ? normalized : null;
}

export function getReleaseFormats(release: Release): string[] {
  const formats: string[] = [];
  const seen = new Set<string>();

  const addFormat = (value: unknown): void => {
    const normalized = normalizeFormatValue(value);
    if (!normalized || seen.has(normalized)) {
      return;
    }
    seen.add(normalized);
    formats.push(normalized);
  };

  addFormat(release.format);

  const extraFormats = (release.extra as Record<string, unknown> | undefined)?.formats;
  if (Array.isArray(extraFormats)) {
    extraFormats.forEach(addFormat);
  } else {
    addFormat(extraFormats);
  }

  return formats;
}
