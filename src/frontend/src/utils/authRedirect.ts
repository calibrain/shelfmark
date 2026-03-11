import { withBasePath } from './basePath';

type LocationLike = {
  hash?: string;
  pathname: string;
  search?: string;
};

const PLACEHOLDER_ORIGIN = 'http://shelfmark.local';
const DEFAULT_RETURN_TO = '/';

const normalizeSearch = (search: string | undefined): string => {
  if (!search) {
    return '';
  }
  return search.startsWith('?') ? search : `?${search}`;
};

const normalizeHash = (hash: string | undefined): string => {
  if (!hash) {
    return '';
  }
  return hash.startsWith('#') ? hash : `#${hash}`;
};

export const sanitizeReturnTo = (value: string | null | undefined): string | null => {
  if (!value) {
    return null;
  }

  const trimmed = value.trim();
  if (!trimmed.startsWith('/') || trimmed.startsWith('//')) {
    return null;
  }

  try {
    const parsed = new URL(trimmed, PLACEHOLDER_ORIGIN);
    if (parsed.origin !== PLACEHOLDER_ORIGIN) {
      return null;
    }
    if (
      parsed.pathname === '/login' ||
      parsed.pathname.startsWith('/login/') ||
      parsed.pathname === '/api' ||
      parsed.pathname.startsWith('/api/')
    ) {
      return null;
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}` || DEFAULT_RETURN_TO;
  } catch {
    return null;
  }
};

export const buildCurrentReturnTo = ({ pathname, search, hash }: LocationLike): string => {
  return sanitizeReturnTo(`${pathname}${normalizeSearch(search)}${normalizeHash(hash)}`) || DEFAULT_RETURN_TO;
};

export const getReturnToFromSearch = (search: string): string => {
  const returnTo = new URLSearchParams(search).get('return_to');
  return sanitizeReturnTo(returnTo) || DEFAULT_RETURN_TO;
};

export const buildLoginRedirectPath = (location: LocationLike): string => {
  const returnTo = buildCurrentReturnTo(location);
  if (returnTo === DEFAULT_RETURN_TO) {
    return '/login';
  }

  const params = new URLSearchParams({ return_to: returnTo });
  return `/login?${params.toString()}`;
};

export const buildOidcLoginUrl = (search: string): string => {
  const returnTo = getReturnToFromSearch(search);
  if (returnTo === DEFAULT_RETURN_TO) {
    return withBasePath('/api/auth/oidc/login');
  }

  const params = new URLSearchParams({ return_to: returnTo });
  return `${withBasePath('/api/auth/oidc/login')}?${params.toString()}`;
};
