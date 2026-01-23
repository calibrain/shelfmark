const normalizeBasePath = (value: string): string => {
  const trimmed = value.trim();
  if (!trimmed) {
    return '/';
  }

  let path = trimmed;
  if (!path.startsWith('/')) {
    path = `/${path}`;
  }

  if (path.length > 1 && path.endsWith('/')) {
    path = path.slice(0, -1);
  }

  return path;
};

const resolveBasePath = (): string => {
  if (typeof document === 'undefined') {
    return '/';
  }

  const baseHref = document.querySelector('base')?.getAttribute('href') || '/';

  try {
    return new URL(baseHref, window.location.origin).pathname;
  } catch {
    return baseHref;
  }
};

const BASE_PATH = normalizeBasePath(resolveBasePath());

export const getBasePath = (): string => BASE_PATH;

export const withBasePath = (path: string): string => {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  if (BASE_PATH === '/') {
    return normalizedPath;
  }
  return `${BASE_PATH}${normalizedPath}`;
};

export const getApiBase = (): string => withBasePath('/api');
