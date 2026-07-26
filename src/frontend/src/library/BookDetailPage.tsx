import { useCallback, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import { Tooltip } from '../components/shared/Tooltip';
import { useDependencyEffect } from '../hooks/useMountEffect';
import {
  downloadLibraryFile,
  getLibraryBook,
  isApiResponseError,
  sendLibraryBookToKindle,
  unlinkLibraryRelease,
} from '../services/api';
import type { Book } from '../types';
import { withBasePath } from '../utils/basePath';
import {
  formatFileSize,
  groupFilesByRelease,
  latestFilesByFormat,
  type BookDetailResponse,
} from './types';

interface BookDetailPageProps {
  autoFindReleases: boolean;
  canFindReleases: boolean;
  onFindReleases: (book: Book) => void;
  onOpenSettings: () => void;
  onShowToast: (message: string, type: 'success' | 'error' | 'info') => void;
}

const toReleaseBook = (book: BookDetailResponse): Book => ({
  id: book.provider_book_id ?? String(book.book_id),
  book_id: book.book_id,
  provider: book.metadata_provider ?? undefined,
  provider_id: book.provider_book_id ?? undefined,
  title: book.title ?? 'Untitled',
  author: book.author ?? '',
  year: book.publish_year?.toString(),
  preview: book.cover_url ?? undefined,
  subtitle: book.subtitle ?? undefined,
  series_name: book.series_name ?? undefined,
  series_position: book.series_position ?? undefined,
});

const dateLabel = (date: string | null): string =>
  date ? new Date(date).toLocaleDateString() : 'date unknown';

export const BookDetailPage = ({
  autoFindReleases,
  canFindReleases,
  onFindReleases,
  onOpenSettings,
  onShowToast,
}: BookDetailPageProps) => {
  const { bookId: rawBookId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const bookId = Number(rawBookId);
  const [book, setBook] = useState<BookDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [autoOpenedFor, setAutoOpenedFor] = useState<number | null>(null);
  const [kindleFormat, setKindleFormat] = useState('epub');
  const findRequested = new URLSearchParams(location.search).get('find') === 'true';

  const load = useCallback(async () => {
    if (!Number.isInteger(bookId) || bookId < 1) {
      setError('Not in your library');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setBook(await getLibraryBook(bookId));
    } catch (caught) {
      if (isApiResponseError(caught) && (caught.status === 403 || caught.status === 404)) {
        setError('Not in your library');
      } else {
        setError(caught instanceof Error ? caught.message : 'Failed to load this book');
      }
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  useDependencyEffect(() => {
    void load();
  }, [load]);

  useDependencyEffect(() => {
    if (
      book &&
      canFindReleases &&
      (findRequested || autoFindReleases) &&
      !book.files.length &&
      (findRequested || !book.in_flight.length) &&
      autoOpenedFor !== book.book_id
    ) {
      setAutoOpenedFor(book.book_id);
      onFindReleases(toReleaseBook(book));
    }
  }, [autoFindReleases, autoOpenedFor, book, canFindReleases, findRequested, onFindReleases]);

  if (loading) return <BookDetailSkeleton />;
  if (error) {
    const unavailable = error === 'Not in your library';
    return (
      <section className="mx-auto max-w-5xl px-4 py-10 text-center sm:px-6 lg:px-8">
        <h1 className="text-xl font-semibold text-(--text)">{error}</h1>
        <button
          type="button"
          className="mt-4 rounded-md border border-(--border-muted) px-3 py-2 text-sm"
          onClick={() => {
            if (unavailable) {
              void navigate('/library');
            } else {
              void load();
            }
          }}
        >
          {unavailable ? 'Back to library' : 'Retry'}
        </button>
      </section>
    );
  }
  if (!book) return null;

  const formats = [...new Set(book.files.flatMap((file) => (file.format ? [file.format] : [])))];
  const kindleFormats = formats.filter((format) => format.toLowerCase() === 'epub');
  const latestFiles = latestFilesByFormat(book.files);
  const defaultKindleFormat = kindleFormats[0] ?? null;
  const selectedKindleFormat = kindleFormats.includes(kindleFormat)
    ? kindleFormat
    : defaultKindleFormat;
  const canSendToKindle = selectedKindleFormat !== null;
  const findReleases = () => onFindReleases(toReleaseBook(book));
  const mutate = async (action: () => Promise<void>, success: string) => {
    try {
      await action();
      onShowToast(success, 'success');
      await load();
    } catch (caught) {
      onShowToast(caught instanceof Error ? caught.message : 'Action failed', 'error');
    }
  };

  return (
    <section className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      {book.in_flight.length > 0 && (
        <p className="mb-5 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-200">
          {book.in_flight.length === 1
            ? 'A release is currently downloading.'
            : `${book.in_flight.length} releases are currently downloading.`}
        </p>
      )}
      <header className="mb-8 flex gap-5">
        {book.cover_url ? (
          <img
            src={withBasePath(book.cover_url)}
            alt=""
            className="h-48 w-32 rounded-lg object-cover shadow"
          />
        ) : (
          <div className="flex h-48 w-32 items-center justify-center rounded-lg bg-(--bg-soft) text-xs text-gray-500">
            No cover
          </div>
        )}
        <div>
          <h1 className="text-2xl font-semibold text-(--text)">{book.title}</h1>
          {book.subtitle && (
            <p className="mt-1 text-gray-600 dark:text-gray-300">{book.subtitle}</p>
          )}
          <p className="mt-2 text-sm text-gray-700 dark:text-gray-200">
            {book.author || 'Unknown author'}
          </p>
          <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
            {[
              book.publish_year,
              book.series_name,
              book.language?.toUpperCase(),
              book.isbn_13 && `ISBN ${book.isbn_13}`,
            ]
              .filter(Boolean)
              .join(' · ')}
          </p>
        </div>
      </header>

      <h2 className="mb-3 text-sm font-semibold text-(--text)">Available formats</h2>
      {latestFiles.length ? (
        <div className="overflow-hidden rounded-xl border border-(--border-muted)">
          {latestFiles.map((file) => (
            <div
              key={file.format}
              className="flex items-center justify-between border-b border-(--border-muted) px-4 py-3 last:border-0"
            >
              <div>
                <strong className="text-sm uppercase">{file.format}</strong>
                <p className="text-xs text-gray-500">
                  Latest from {file.indexer_display_name || 'unknown source'} ·{' '}
                  {dateLabel(file.downloaded_at)}
                </p>
              </div>
              <button
                type="button"
                className="rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white"
                onClick={() =>
                  void mutate(
                    () => downloadLibraryFile(book.book_id, { format: file.format ?? undefined }),
                    'Download started',
                  )
                }
              >
                Download
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-(--border-muted) p-6 text-center">
          <p className="text-sm text-gray-600 dark:text-gray-300">No files on disk yet.</p>
          {canFindReleases && (
            <button
              type="button"
              className="mt-3 rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white"
              onClick={findReleases}
            >
              Find Releases
            </button>
          )}
        </div>
      )}

      <details className="mt-7">
        <summary className="cursor-pointer rounded-xl border border-(--border-muted) px-4 py-3 text-sm font-medium">
          Releases
        </summary>
        <div className="mt-3 space-y-3">
          {groupFilesByRelease(book.files).map(([taskId, files]) => (
            <div key={taskId} className="rounded-xl border border-(--border-muted)">
              <div className="flex items-center justify-between border-b border-(--border-muted) px-4 py-3">
                <span className="text-sm">
                  {files[0].indexer_display_name || 'Unknown source'} ·{' '}
                  {dateLabel(files[0].downloaded_at)}
                </span>
                {files.some((file) => file.downloadable_by_me) && (
                  <button
                    type="button"
                    className="text-xs text-rose-700 dark:text-rose-300"
                    onClick={() =>
                      void mutate(
                        () => unlinkLibraryRelease(book.book_id, files[0].history_id),
                        'Release unlinked',
                      )
                    }
                  >
                    Unlink release
                  </button>
                )}
              </div>
              {files.map((file) => (
                <div key={file.history_id} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <span className="uppercase">{file.format || 'unknown'}</span>
                  <span className="text-xs text-gray-500">{formatFileSize(file.size)}</span>
                  {file.protocol && <span className="text-xs text-gray-500">{file.protocol}</span>}
                  <button
                    type="button"
                    className="ml-auto text-xs text-sky-700 dark:text-sky-300"
                    onClick={() =>
                      void mutate(
                        () => downloadLibraryFile(book.book_id, { historyId: file.history_id }),
                        'Download started',
                      )
                    }
                  >
                    Download file
                  </button>
                </div>
              ))}
            </div>
          ))}
          {book.in_flight.map((file) => (
            <p
              key={file.history_id}
              className="rounded border border-amber-300 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:text-amber-200"
            >
              {file.source_display_name || 'A release'} is still downloading.
            </p>
          ))}
        </div>
      </details>

      <div className="mt-7 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-(--border-muted) p-4">
        <div>
          <h2 className="text-sm font-semibold">Send to Kindle</h2>
          <button
            type="button"
            className="mt-1 text-xs text-emerald-700 underline dark:text-emerald-300"
            onClick={onOpenSettings}
          >
            Configure Kindle email in Settings
          </button>
        </div>
        <div className="flex gap-2">
          <select
            value={selectedKindleFormat ?? 'auto'}
            disabled={!kindleFormats.length}
            onChange={(event) => setKindleFormat(event.target.value)}
            className="rounded border border-(--border-muted) bg-transparent px-2 text-sm"
          >
            <option value="auto">Auto (EPUB)</option>
            {kindleFormats.map((format) => (
              <option key={format} value={format}>
                {format.toUpperCase()}
              </option>
            ))}
          </select>
          <Tooltip content="Send-to-Kindle defaults to EPUB. Choose an on-disk format to override it.">
            <button
              type="button"
              disabled={!canSendToKindle}
              className="rounded bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-gray-400"
              onClick={() => {
                if (selectedKindleFormat)
                  void mutate(async () => {
                    await sendLibraryBookToKindle(book.book_id, selectedKindleFormat);
                  }, 'Sent to Kindle');
              }}
            >
              {canSendToKindle
                ? `Send ${selectedKindleFormat.toUpperCase()} to Kindle`
                : 'No Kindle-compatible format'}
            </button>
          </Tooltip>
        </div>
      </div>
      {canFindReleases && (
        <div className="mt-5 text-right">
          <button
            type="button"
            className="text-sm text-emerald-700 underline dark:text-emerald-300"
            onClick={findReleases}
          >
            Find Releases
          </button>
        </div>
      )}
    </section>
  );
};

const BookDetailSkeleton = () => (
  <section className="mx-auto max-w-5xl animate-pulse px-4 py-6 sm:px-6 lg:px-8">
    <div className="h-48 w-32 rounded bg-gray-200 dark:bg-gray-700" />
    <div className="mt-6 h-20 rounded-xl bg-gray-200 dark:bg-gray-700" />
  </section>
);
