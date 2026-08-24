import type { Release } from '../types';

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

  const extraFormats = release.extra?.formats;
  if (Array.isArray(extraFormats)) {
    extraFormats.forEach(addFormat);
  } else {
    addFormat(extraFormats);
  }

  return formats;
}

/**
 * Format tokens the indexer declared but the backend could not map to a known
 * book/audiobook format (e.g. MyAnonamouse "[ENG / AVI]"). Such a release will
 * download but fail post-processing, so the UI warns instead of showing a bare
 * content-type icon.
 */
export function getUnrecognizedReleaseFormats(release: Release): string[] {
  const raw = release.extra?.unrecognized_formats;
  const values = Array.isArray(raw) ? raw : [raw];
  const formats: string[] = [];
  const seen = new Set<string>();

  values.forEach((value) => {
    const normalized = normalizeFormatValue(value);
    if (!normalized || seen.has(normalized)) {
      return;
    }
    seen.add(normalized);
    formats.push(normalized);
  });

  return formats;
}
