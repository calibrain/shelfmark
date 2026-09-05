const SEARCH_BY_STORAGE_KEY = 'shelfmark_search_by';

/**
 * Reads the client-side default "Search By" target the user last picked.
 *
 * localStorage (not a user account/setting) so it works without any server-side
 * user management, and unlike a cookie it isn't sent on every HTTP request -
 * it's a purely browser-side UI preference.
 */
export const getSearchByPreference = (): string | null => {
  try {
    return window.localStorage.getItem(SEARCH_BY_STORAGE_KEY);
  } catch {
    // localStorage can throw when storage is disabled or the quota is exhausted
    return null;
  }
};

export const setSearchByPreference = (value: string): void => {
  try {
    window.localStorage.setItem(SEARCH_BY_STORAGE_KEY, value);
  } catch {
    // Preference is best-effort - a failure here must not break search
  }
};
