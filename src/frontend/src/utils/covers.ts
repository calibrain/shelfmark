const COVER_PROXY_PATH = '/api/covers/';
const LOCAL_URL_BASE = 'http://shelfmark.local';
const DEFAULT_COVER_FORMAT = 'webp';
const MAX_COVER_DIMENSION = 1024;

type CoverFormat = 'jpeg' | 'png' | 'webp';

interface SizedCoverUrlOptions {
  width?: number;
  height?: number;
  format?: CoverFormat;
}

const normalizeDimension = (value?: number) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return undefined;
  }

  const rounded = Math.round(value);
  if (rounded <= 0) {
    return undefined;
  }

  return Math.min(rounded, MAX_COVER_DIMENSION);
};

export const getSizedCoverUrl = (
  preview?: string,
  { width, height, format = DEFAULT_COVER_FORMAT }: SizedCoverUrlOptions = {},
) => {
  if (!preview) {
    return preview;
  }

  const isRelativeUrl = preview.startsWith('/');

  let url: URL;
  try {
    url = new URL(preview, LOCAL_URL_BASE);
  } catch {
    return preview;
  }

  if (!url.pathname.includes(COVER_PROXY_PATH)) {
    return preview;
  }

  const normalizedWidth = normalizeDimension(width);
  const normalizedHeight = normalizeDimension(height);

  if (normalizedWidth !== undefined) {
    url.searchParams.set('w', String(normalizedWidth));
  }

  if (normalizedHeight !== undefined) {
    url.searchParams.set('h', String(normalizedHeight));
  }

  if (format) {
    url.searchParams.set('format', format);
  }

  const search = url.searchParams.toString();
  const relativeUrl = `${url.pathname}${search ? `?${search}` : ''}${url.hash}`;
  return isRelativeUrl ? relativeUrl : url.toString();
};
