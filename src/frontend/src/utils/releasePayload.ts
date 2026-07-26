import type { Book, ContentType, Release } from '../types';

export const buildReleaseDataFromMetadataRelease = (
  book: Book,
  release: Release,
  contentType: ContentType,
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
