import type { Book, RequestRecord } from '../types';

const toOptionalText = (value: unknown): string | undefined => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return undefined;
};

const toOptionalNumber = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
};

const asRecord = (value: Record<string, unknown> | null | undefined): Record<string, unknown> => {
  if (value && typeof value === 'object') {
    return value;
  }
  return {};
};

export const bookFromRequestData = (bookData: Record<string, unknown> | null | undefined): Book => {
  const row = asRecord(bookData);
  const providerId = toOptionalText(row.provider_id);
  const title = toOptionalText(row.title) || 'Unknown title';

  return {
    id: providerId || title || 'request',
    title,
    author: toOptionalText(row.author) || 'Unknown author',
    source: toOptionalText(row.source),
    provider: toOptionalText(row.provider),
    provider_id: providerId,
    preview: toOptionalText(row.preview),
    year: toOptionalText(row.year),
    series_name: toOptionalText(row.series_name),
    series_position: toOptionalNumber(row.series_position),
    subtitle: toOptionalText(row.subtitle),
    source_url: toOptionalText(row.source_url),
  };
};

export const bookFromRequestRecord = (record: RequestRecord): Book => {
  const book = bookFromRequestData(record.book_data);
  const bookId = toOptionalText(record.book_id);

  return {
    ...book,
    id: bookId || book.id,
    title: toOptionalText(record.book_title) || book.title,
    author: toOptionalText(record.book_author) || book.author,
    preview: toOptionalText(record.book_cover_url) || book.preview,
    book_id: toOptionalNumber(record.book_id),
  };
};
