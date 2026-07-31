export interface LibraryFile {
  history_id: number;
  task_id: string;
  format: string | null;
  size: string | null;
  indexer_display_name: string | null;
  protocol: string | null;
  downloaded_at: string | null;
  downloadable_by_me: boolean;
}

export interface InFlightDownload {
  history_id: number;
  task_id: string;
  format: string | null;
  source_display_name: string | null;
}

export interface BookDetailResponse {
  book_id: number;
  metadata_provider: string | null;
  provider_book_id: string | null;
  title: string | null;
  author: string | null;
  subtitle: string | null;
  publish_year: number | null;
  isbn_13: string | null;
  cover_url: string | null;
  series_name: string | null;
  series_position: number | null;
  language: string | null;
  metadata_json: {
    description?: string | null;
    provider_display_name?: string | null;
    display_fields?: Array<{ label: string; value: string; icon?: string | null }>;
  };
  in_my_library: boolean;
  files: LibraryFile[];
  in_flight: InFlightDownload[];
}

export interface LibraryBookSummary {
  book_id: number;
  title: string | null;
  author: string | null;
  cover_url: string | null;
  formats_on_disk: Array<{ format: string | null; size: string | null }>;
  added_at: string | null;
}

export interface LibraryBooksResponse {
  books: LibraryBookSummary[];
}

export interface LibraryBookAvailabilityEvent {
  book_id: number;
  task_id: string;
  availability: 'available';
}

export interface LibraryPurgePreview {
  users: Array<{ display_name: string | null; username: string }>;
}

export const latestFilesByFormat = (files: LibraryFile[]): LibraryFile[] => {
  const latest = new Map<string, LibraryFile>();
  for (const file of files) {
    if (!file.format) continue;
    const current = latest.get(file.format);
    if (
      !current ||
      (file.downloaded_at ?? '') > (current.downloaded_at ?? '') ||
      (file.downloaded_at === current.downloaded_at && file.history_id > current.history_id)
    ) {
      latest.set(file.format, file);
    }
  }
  return [...latest.values()].toSorted((a, b) => (a.format ?? '').localeCompare(b.format ?? ''));
};

const latestTimestamp = (entries: LibraryFile[]): number =>
  Math.max(...entries.map((entry) => Date.parse(entry.downloaded_at ?? '') || 0));

export const groupFilesByRelease = (files: LibraryFile[]): Array<[string, LibraryFile[]]> => {
  const groups = new Map<string, LibraryFile[]>();
  for (const file of files) {
    groups.set(file.task_id, [...(groups.get(file.task_id) ?? []), file]);
  }
  return [...groups.entries()].toSorted(
    ([, left], [, right]) => latestTimestamp(right) - latestTimestamp(left),
  );
};

export const formatFileSize = (size: string | null): string => {
  if (size === null || !/^\d+$/.test(size)) return size ?? '';

  const bytes = Number(size);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return unitIndex === 0 ? `${value} B` : `${value.toFixed(1)} ${units[unitIndex]}`;
};
