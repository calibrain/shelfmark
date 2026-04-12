import type { SelectFieldConfig } from '../types/settings';

const THEME_PREFERENCE_KEY = 'preferred-theme';
const DEFAULT_THEME_PREFERENCE = 'auto';

export const THEME_FIELD: SelectFieldConfig = {
  type: 'SelectField',
  key: '_THEME',
  label: 'Theme',
  description: 'Choose your preferred color scheme.',
  value: DEFAULT_THEME_PREFERENCE,
  options: [
    { value: 'light', label: 'Light' },
    { value: 'dark', label: 'Dark' },
    { value: 'auto', label: 'Auto (System)' },
  ],
};

export function getStoredThemePreference(): string {
  try {
    return localStorage.getItem(THEME_PREFERENCE_KEY) || DEFAULT_THEME_PREFERENCE;
  } catch {
    return DEFAULT_THEME_PREFERENCE;
  }
}

function applyThemePreference(theme: string): void {
  let effectiveTheme = theme;
  if (theme === 'auto') {
    effectiveTheme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', effectiveTheme);
  document.documentElement.style.colorScheme = effectiveTheme;
}

export function setThemePreference(theme: string): void {
  try {
    localStorage.setItem(THEME_PREFERENCE_KEY, theme);
  } catch {
    // localStorage may be unavailable in private browsing
  }
  applyThemePreference(theme);
}
