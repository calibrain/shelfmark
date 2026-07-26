import type { Book, RequestRecord, StatusData } from '../../types';
import { STATUS_LABELS, isActiveDownloadStatus } from './activityStyles.js';
import type { ActivityItem, ActivityVisualStatus } from './activityTypes';

export type DownloadStatusKey = Extract<
  keyof StatusData,
  'queued' | 'resolving' | 'locating' | 'downloading' | 'complete' | 'error' | 'cancelled'
>;

const toText = (value: unknown, fallback: string): string => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  return fallback;
};

const toOptionalText = (value: unknown): string | undefined => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return undefined;
};

const toNumber = (value: unknown, fallback = 0): number => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  return fallback;
};

const toEpochMillis = (value: unknown): number => {
  const parsed = toNumber(value, 0);
  if (parsed <= 0) {
    return 0;
  }
  // Queue `added_time` values are epoch seconds; request/history timestamps are ms.
  // Normalize to ms so sorting is consistent across merged activity items.
  if (parsed >= 1_000_000_000 && parsed < 1_000_000_000_000) {
    return parsed * 1000;
  }
  return parsed;
};

const toSourceLabel = (value: unknown): string | undefined => {
  const text = toOptionalText(value);
  if (!text) {
    return undefined;
  }
  return text
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

const joinMetaParts = (parts: Array<string | undefined>): string => {
  return parts.filter((part): part is string => Boolean(part && part.trim())).join(' · ');
};

const statusKeyToVisualStatus = (statusKey: DownloadStatusKey): ActivityVisualStatus => {
  if (statusKey === 'queued') return 'queued';
  if (statusKey === 'resolving') return 'resolving';
  if (statusKey === 'locating') return 'locating';
  if (statusKey === 'downloading') return 'downloading';
  if (statusKey === 'complete') return 'complete';
  if (statusKey === 'error') return 'error';
  return 'cancelled';
};

const getDownloadProgress = (
  status: ActivityVisualStatus,
  bookProgress: unknown,
): number | undefined => {
  if (status === 'queued') return 5;
  if (status === 'resolving') return 15;
  if (status === 'locating') return 90;
  if (status === 'downloading') {
    const progress =
      typeof bookProgress === 'number' ? Math.max(0, Math.min(100, bookProgress)) : 0;
    return Math.max(0, Math.min(100, 20 + progress * 0.8));
  }
  return undefined;
};

export const downloadToActivityItem = (book: Book, statusKey: DownloadStatusKey): ActivityItem => {
  const visualStatus = statusKeyToVisualStatus(statusKey);
  const requestId =
    typeof book.request_id === 'number' && Number.isFinite(book.request_id) && book.request_id > 0
      ? Math.trunc(book.request_id)
      : undefined;
  const metaLine = joinMetaParts([
    toOptionalText(book.format)?.toUpperCase(),
    toOptionalText(book.size),
    toOptionalText(book.source_display_name) || toSourceLabel(book.source),
    toOptionalText(book.username),
  ]);
  const progress = getDownloadProgress(visualStatus, book.progress);
  const statusDetail = toOptionalText(book.status_message);
  const downloadRetryAvailable = book.retry_available === true;

  return {
    id: book.id,
    kind: 'download',
    visualStatus,
    title: toText(book.title, 'Unknown title'),
    author: toText(book.author, 'Unknown author'),
    preview: toOptionalText(book.preview),
    metaLine,
    statusLabel: STATUS_LABELS[visualStatus],
    statusDetail,
    progress,
    progressAnimated: isActiveDownloadStatus(visualStatus),
    timestamp: toEpochMillis(book.added_time),
    username: toOptionalText(book.username),
    downloadBookId: book.id,
    downloadRetryAvailable,
    downloadPath: toOptionalText(book.download_path),
    sizeRaw: toOptionalText(book.size),
    requestId,
  };
};

const parseRecordData = (
  value: Record<string, unknown> | null | undefined,
): Record<string, unknown> => value ?? {};

export interface RequestBookIdentity {
  id: string;
  title: string;
  author: string;
  coverUrl?: string;
}

export const getRequestBookIdentity = (record: RequestRecord): RequestBookIdentity => {
  const bookData = parseRecordData(record.book_data);
  const releaseData = parseRecordData(record.release_data);
  const id =
    toOptionalText(record.book_id) ||
    toOptionalText(bookData.book_id) ||
    toOptionalText(bookData.id) ||
    toOptionalText(bookData.provider_id) ||
    `request-${record.id}`;

  return {
    id,
    title: toText(record.book_title ?? bookData.title ?? releaseData.title, 'Unknown title'),
    author: toText(record.book_author ?? bookData.author ?? releaseData.author, 'Unknown author'),
    coverUrl:
      toOptionalText(record.book_cover_url) ||
      toOptionalText(bookData.preview) ||
      toOptionalText(bookData.cover_url) ||
      toOptionalText(releaseData.preview),
  };
};

const requestStatusToVisualStatus = (status: RequestRecord['status']): ActivityVisualStatus => {
  if (status === 'pending') return 'pending';
  if (status === 'fulfilled') return 'fulfilled';
  if (status === 'rejected') return 'rejected';
  return 'cancelled';
};

const buildRequestMetaLine = (
  record: RequestRecord,
  bookData: Record<string, unknown>,
  releaseData: Record<string, unknown>,
  viewerRole: 'user' | 'admin',
): string => {
  if (viewerRole === 'user') {
    const contentType = toOptionalText(record.content_type || bookData.content_type)?.toLowerCase();
    return contentType === 'audiobook' ? 'Audiobook request' : 'Book request';
  }

  if (record.request_level === 'book') {
    const contentType = toOptionalText(record.content_type || bookData.content_type)?.toLowerCase();
    const requestTypeLabel = contentType === 'audiobook' ? 'Audiobook request' : 'Book request';
    const username = viewerRole === 'admin' ? toOptionalText(record.username) : undefined;
    return joinMetaParts([requestTypeLabel, username]);
  }

  const format = toOptionalText(releaseData.format)?.toUpperCase();
  const size = toOptionalText(releaseData.size);
  const source = toSourceLabel(releaseData.source || record.source_hint);
  const username = viewerRole === 'admin' ? toOptionalText(record.username) : undefined;

  const line = joinMetaParts([format, size, source, username]);
  return line || joinMetaParts(['Release request', username]);
};

export const requestToActivityItem = (
  record: RequestRecord,
  viewerRole: 'user' | 'admin',
): ActivityItem => {
  const visualStatus = requestStatusToVisualStatus(record.status);
  const bookData = parseRecordData(record.book_data);
  const releaseData = parseRecordData(record.release_data);
  const book = getRequestBookIdentity(record);

  const timestamp = Number.isFinite(Date.parse(record.created_at))
    ? Date.parse(record.created_at)
    : 0;

  return {
    id: `request-${record.id}`,
    kind: 'request',
    visualStatus,
    title: book.title,
    author: book.author,
    preview: book.coverUrl,
    metaLine: buildRequestMetaLine(record, bookData, releaseData, viewerRole),
    statusLabel: STATUS_LABELS[visualStatus],
    adminNote: toOptionalText(record.admin_note),
    timestamp,
    username: toOptionalText(record.username),
    requestId: record.id,
    requestLevel: record.request_level,
    requestNote: toOptionalText(record.note),
    requestRecord: record,
  };
};
