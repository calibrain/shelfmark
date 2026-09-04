const SEARCH_BY_COOKIE_KEY = 'shelfmark_search_by';
const SEARCH_BY_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365; // 1 year

/**
 * Reads the client-side cookie storing the user's last-used "Search By" target.
 *
 * A cookie (not a user account/setting) so it works without any server-side
 * user management - it's just a UI preference, not sensitive data.
 */
export const getSearchByCookie = (): string | null => {
  try {
    const match = document.cookie.match(new RegExp(`(?:^|; )${SEARCH_BY_COOKIE_KEY}=([^;]*)`));
    return match ? decodeURIComponent(match[1]) : null;
  } catch {
    return null;
  }
};

export const setSearchByCookie = (value: string): void => {
  try {
    document.cookie = `${SEARCH_BY_COOKIE_KEY}=${encodeURIComponent(value)}; path=/; max-age=${SEARCH_BY_COOKIE_MAX_AGE_SECONDS}; SameSite=Lax`;
  } catch {
    // document.cookie may be unavailable in some environments
  }
};
