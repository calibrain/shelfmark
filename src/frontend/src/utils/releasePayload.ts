import type { DownloadReleasePayload } from '../services/api';
import type { Book, ContentType, PackBook, Release } from '../types';

export interface ReleaseDownloadOptions {
  /** Ask post-processing to split the release into one book per subfolder/file. */
  multiBook?: boolean;
  /** The split the user approved in the pack review panel. */
  bookPlan?: PackBook[];
}

/** Build the body for /api/releases/download (and /api/releases/inspect). */
export function buildReleaseDownloadPayload(
  book: Book,
  release: Release,
  releaseContentType: ContentType,
  options: ReleaseDownloadOptions = {},
): DownloadReleasePayload {
  const isManual = book.provider === 'manual';
  const releasePreview =
    typeof release.extra?.preview === 'string' ? release.extra.preview : undefined;
  const releaseAuthor =
    typeof release.extra?.author === 'string' ? release.extra.author : undefined;

  const payload: DownloadReleasePayload = {
    source: release.source,
    source_id: release.source_id,
    title: isManual ? release.title : book.title,
    author: isManual ? releaseAuthor || '' : book.author,
    year: book.year,
    format: release.format,
    size: release.size,
    size_bytes: release.size_bytes,
    download_url: release.download_url,
    protocol: release.protocol,
    indexer: release.indexer,
    seeders: release.seeders,
    extra: release.extra,
    preview: isManual ? releasePreview || undefined : book.preview,
    content_type: releaseContentType,
    series_name: book.series_name,
    series_position: book.series_position,
    subtitle: book.subtitle,
    // From the release, never the book: book.language is the provider's
    // canonical edition, which would mislabel a translated release.
    language: release.language ?? undefined,
  };

  if (options.multiBook || options.bookPlan) {
    payload.multi_book = true;
  }
  if (options.bookPlan) {
    payload.book_plan = options.bookPlan;
  }
  return payload;
}
