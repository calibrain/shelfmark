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

// Lazy initialization to ensure DOM is ready when base path is resolved
let _basePath: string | null = null;

export const getBasePath = (): string => {
  if (_basePath === null) {
    _basePath = normalizeBasePath(resolveBasePath());
  }
  return _basePath;
};

export const withBasePath = (path: string): string => {
  const basePath = getBasePath();
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  if (basePath === '/') {
    return normalizedPath;
  }
  return `${basePath}${normalizedPath}`;
};

export const getApiBase = (): string => withBasePath('/api');
