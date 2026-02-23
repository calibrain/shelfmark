import {
  Book,
  ContentType,
  CreateRequestPayload,
  Release,
  RequestPolicyMode,
} from '../types';

export const toContentType = (value: ContentType | string): ContentType => {
  return String(value).trim().toLowerCase() === 'audiobook' ? 'audiobook' : 'ebook';
};

export const buildMetadataBookRequestData = (book: Book, contentType: ContentType) => {
  return {
    title: book.title || 'Unknown title',
    author: book.author || 'Unknown author',
    content_type: contentType,
    provider: book.provider || 'metadata',
    provider_id: book.provider_id || book.id,
    year: book.year,
    preview: book.preview,
    series_name: book.series_name,
    series_position: book.series_position,
    series_count: book.series_count,
    subtitle: book.subtitle,
    source_url: book.source_url,
  };
};

export const buildDirectBookRequestData = (book: Book) => {
  return {
    title: book.title || 'Unknown title',
    author: book.author || 'Unknown author',
    content_type: 'ebook' as const,
    provider: 'direct_download',
    provider_id: book.id,
    year: book.year,
    format: book.format,
    size: book.size,
    preview: book.preview,
    source: book.source || 'direct_download',
    source_url: book.source_url,
  };
};

export const buildReleaseDataFromMetadataRelease = (
  book: Book,
  release: Release,
  contentType: ContentType
) => {
  return {
    source: release.source,
    source_id: release.source_id,
    title: book.title || release.title || 'Unknown title',
    author: book.author,
    year: book.year,
    format: release.format,
    size: release.size,
    size_bytes: release.size_bytes,
    download_url: release.download_url,
    protocol: release.protocol,
    indexer: release.indexer,
    seeders: release.seeders,
    extra: release.extra,
    preview: book.preview,
    content_type: contentType,
    series_name: book.series_name,
    series_position: book.series_position,
    series_count: book.series_count,
    subtitle: book.subtitle,
  };
};

export const buildReleaseDataFromDirectBook = (book: Book) => {
  return {
    source: 'direct_download',
    source_id: book.id,
    title: book.title || 'Unknown title',
    author: book.author,
    year: book.year,
    format: book.format,
    size: book.size,
    preview: book.preview,
    content_type: 'ebook' as const,
  };
};

export const buildDirectRequestPayload = (
  book: Book,
  mode: Extract<RequestPolicyMode, 'request_release' | 'request_book'>
): CreateRequestPayload => {
  const bookData = buildDirectBookRequestData(book);

  // In direct mode, every result already represents a concrete downloadable release.
  // Even when policy defaults resolve to request_book, attach the selected release so
  // admins can approve immediately or browse alternatives from the same record.
  if (mode === 'request_book') {
    return {
      book_data: bookData,
      release_data: buildReleaseDataFromDirectBook(book),
      context: {
        source: 'direct_download',
        content_type: 'ebook',
        request_level: 'release',
      },
    };
  }

  return {
    book_data: bookData,
    release_data: buildReleaseDataFromDirectBook(book),
    context: {
      source: 'direct_download',
      content_type: 'ebook',
      request_level: 'release',
    },
  };
};

export const getRequestSuccessMessage = (payload: CreateRequestPayload): string => {
  const bookData = payload.book_data || {};
  const releaseData = payload.release_data || {};
  const title =
    (typeof bookData.title === 'string' && bookData.title.trim()) ||
    (typeof releaseData.title === 'string' && releaseData.title.trim()) ||
    'Untitled';
  return `Request submitted: ${title}`;
};
