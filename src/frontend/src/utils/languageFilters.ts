import { Language } from '../types';

export const LANGUAGE_OPTION_DEFAULT = 'default';
export const LANGUAGE_OPTION_ALL = 'all';

export const normalizeLanguageSelection = (selected: string[]): string[] => {
  const sanitized = (selected ?? []).filter(Boolean);

  if (sanitized.length === 0) {
    return [LANGUAGE_OPTION_DEFAULT];
  }

  const unique: string[] = [];
  const seen = new Set<string>();

  for (const value of sanitized) {
    if (!seen.has(value)) {
      unique.push(value);
      seen.add(value);
    }
  }

  if (unique.includes(LANGUAGE_OPTION_ALL)) {
    return [LANGUAGE_OPTION_ALL];
  }

  return unique.length ? unique : [LANGUAGE_OPTION_DEFAULT];
};

export const getLanguageFilterValues = (
  selection: string[],
  supportedLanguages: Language[],
  defaultLanguageCodes: string[] = [],
): string[] | null => {
  if (!selection || selection.length === 0) {
    return null;
  }

  const uniqueSelection = Array.from(new Set(selection.filter(Boolean)));

  if (uniqueSelection.includes(LANGUAGE_OPTION_ALL)) {
    return [LANGUAGE_OPTION_ALL];
  }

  const onlyDefaultSelected =
    uniqueSelection.length === 1 && uniqueSelection[0] === LANGUAGE_OPTION_DEFAULT;
  if (onlyDefaultSelected) {
    return null;
  }

  const supportedCodes = new Set(supportedLanguages.map(lang => lang.code));
  const defaultCodes = defaultLanguageCodes.filter(code => supportedCodes.has(code));
  const resolved = new Set<string>();

  uniqueSelection.forEach(code => {
    if (code === LANGUAGE_OPTION_DEFAULT) {
      defaultCodes.forEach(defaultCode => resolved.add(defaultCode));
      return;
    }

    if (supportedCodes.has(code)) {
      resolved.add(code);
    }
  });

  return resolved.size ? Array.from(resolved) : null;
};

/**
 * Resolve language selection for /api/releases requests.
 * - undefined: use backend defaults
 * - ["all"]: disable language filtering
 * - ["en", ...]: explicit filter list
 */
export const getReleaseSearchLanguageParams = (
  selection: string[],
  supportedLanguages: Language[],
  defaultLanguageCodes: string[] = [],
): string[] | undefined => {
  const resolved = getLanguageFilterValues(selection, supportedLanguages, defaultLanguageCodes);
  return resolved === null ? undefined : resolved;
};

export const formatDefaultLanguageLabel = (
  languageCodes: string[],
  supportedLanguages: Language[],
): string => {
  if (!languageCodes || languageCodes.length === 0) {
    return 'Default (env config)';
  }

  const languageNames = supportedLanguages
    .filter(lang => languageCodes.includes(lang.code))
    .map(lang => lang.language);

  if (languageNames.length === 0) {
    return 'Default (env config)';
  }

  const joined = languageNames.slice(0, 3).join(', ');
  const suffix = languageNames.length > 3 ? '…' : '';
  return `Default (${joined}${suffix})`;
};

/**
 * Build a mapping from language names to codes for normalization.
 * Handles both directions: "english" -> "en" and "en" -> "en"
 */
export const buildLanguageNormalizer = (languages: Language[]): Map<string, string> => {
  const map = new Map<string, string>();
  for (const lang of languages) {
    const code = lang.code.toLowerCase();
    map.set(code, code); // code -> code
    map.set(lang.language.toLowerCase(), code); // name -> code
  }
  return map;
};

/**
 * Check if ALL languages in a multi-language release match the selected filter.
 * Multi-language releases use separators like comma, slash, plus, or ampersand
 * (e.g., "English, Spanish", "English/Spanish", "English + Spanish", "English & Spanish").
 *
 * @param releaseLang - Language string from the release (can be code or full name)
 * @param selectedCodes - Array of selected ISO language codes
 * @param languageNormalizer - Optional map to normalize language names to codes
 */
export const releaseLanguageMatchesFilter = (
  releaseLang: string | undefined,
  selectedCodes: string[] | null,
  languageNormalizer?: Map<string, string>,
): boolean => {
  if (!releaseLang || !selectedCodes) {
    return true;
  }
  if (selectedCodes.includes(LANGUAGE_OPTION_ALL)) {
    return true;
  }

  // Split by common multi-language separators: comma, slash, plus, ampersand
  const releaseParts = releaseLang.split(/[,/+&]/).map(l => l.trim().toLowerCase()).filter(Boolean);

  // Normalize release language parts to codes (handles both "en" and "english")
  const releaseCodes = releaseParts.map(part => {
    if (languageNormalizer) {
      return languageNormalizer.get(part) ?? part;
    }
    return part;
  });

  const selectedSet = new Set(selectedCodes.map(c => c.toLowerCase()));
  return releaseCodes.every(code => selectedSet.has(code));
};
