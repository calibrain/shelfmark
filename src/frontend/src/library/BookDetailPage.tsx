import { useCallback, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import { useDependencyEffect } from '../hooks/useMountEffect';
import {
  cancelRequest,
  createLibraryRequest,
  downloadLibraryFile,
  getLibraryBook,
  isApiResponseError,
  listLibraryRequests,
  sendLibraryBookToKindle,
  unlinkLibraryRelease,
} from '../services/api';
import type { Book, RequestRecord } from '../types';
import { withBasePath } from '../utils/basePath';
import {
  formatFileSize,
  groupFilesByRelease,
  latestFilesByFormat,
  type BookDetailResponse,
  type LibraryFile,
} from './types';

interface BookDetailPageProps {
  autoFindReleases: boolean;
  canFindReleases: boolean;
  isRequestOnly: boolean;
  onFindReleases: (book: Book) => void;
  onOpenSettings: () => void;
  onShowToast: (message: string, type: 'success' | 'error' | 'info') => void;
}

interface BookDetailLocationState {
  autoFindReleases?: boolean;
}

const hasAutoFindReleasesIntent = (state: unknown): state is BookDetailLocationState =>
  typeof state === 'object' &&
  state !== null &&
  Object.getOwnPropertyDescriptor(state, 'autoFindReleases')?.value === true;

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

export const shouldAutoFindReleases = ({
  canFindReleases,
  autoFindReleases,
  firstAddIntent,
  hasFiles,
  hasInFlight,
  alreadyOpened,
}: {
  canFindReleases: boolean;
  autoFindReleases: boolean;
  firstAddIntent: boolean;
  hasFiles: boolean;
  hasInFlight: boolean;
  alreadyOpened: boolean;
}): boolean =>
  canFindReleases &&
  autoFindReleases &&
  firstAddIntent &&
  !hasFiles &&
  !hasInFlight &&
  !alreadyOpened;

export const bookMembershipLabel = (inMyLibrary: boolean): string =>
  inMyLibrary ? 'In your library' : 'Not in your library';

const RequestState = ({
  request,
  onRequest,
  onCancel,
}: {
  request: RequestRecord | undefined;
  onRequest: () => void;
  onCancel: () => void;
}) => {
  if (!request) {
    return (
      <div className="mt-4 rounded-lg bg-(--bg-soft) px-4 py-4">
        <p className="text-sm text-gray-600 dark:text-gray-300">
          No files are available yet. Request this book and an administrator will find a release.
        </p>
        <button
          type="button"
          className="mt-3 cursor-pointer rounded-md bg-violet-700 px-3 py-2 text-sm font-medium text-white hover:bg-violet-800"
          onClick={onRequest}
        >
          Request this book
        </button>
      </div>
    );
  }

  const labels = {
    pending: 'Request pending',
    cancelled: 'Request cancelled',
    fulfilled: 'Book available',
    rejected: 'Request declined',
  } as const;
  return (
    <div className="mt-4 rounded-lg bg-(--bg-soft) px-4 py-4">
      <p className="text-sm font-medium text-(--text)">{labels[request.status]}</p>
      {request.status === 'pending' && (
        <button
          type="button"
          className="hover-action mt-3 cursor-pointer rounded-md px-2 py-1 text-sm font-medium text-rose-700 dark:text-rose-300"
          onClick={onCancel}
        >
          Cancel request
        </button>
      )}
    </div>
  );
};

const AvailableFiles = ({
  book,
  canFindReleases,
  onDownload,
  onFindReleases,
  onOpenSettings,
  onSendToKindle,
  onUnlinkRelease,
}: {
  book: BookDetailResponse;
  canFindReleases: boolean;
  onDownload: (file: LibraryFile) => void;
  onFindReleases: () => void;
  onOpenSettings: () => void;
  onSendToKindle: (format: string) => void;
  onUnlinkRelease: (file: LibraryFile) => void;
}) => {
  const [kindleFormat, setKindleFormat] = useState('epub');
  const releases = groupFilesByRelease(book.files);
  const latestFiles = latestFilesByFormat(book.files);
  const kindleFiles = latestFilesByFormat(book.files.filter((file) => file.downloadable_by_me));
  const kindleFormats = kindleFiles
    .map((file) => file.format)
    .filter((format): format is string => format?.toLowerCase() === 'epub');
  const selectedKindleFormat = kindleFormats.includes(kindleFormat) ? kindleFormat : null;

  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-(--text)">Available files</h2>
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
            The newest downloaded file for each format.
          </p>
        </div>
        {canFindReleases && (
          <button
            type="button"
            className="hover-action cursor-pointer rounded-md px-2 py-1 text-sm font-medium text-emerald-700 dark:text-emerald-300"
            onClick={onFindReleases}
          >
            Find another release
          </button>
        )}
      </div>
      {latestFiles.length > 0 ? (
        <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="rounded-lg bg-(--bg-soft) px-4 py-2">
            {latestFiles.map((file) => (
              <div
                key={file.history_id}
                className="flex items-center gap-3 border-b border-(--border-muted) py-3 last:border-0"
              >
                <span className="w-16 text-sm font-semibold text-(--text)">
                  {file.format?.toUpperCase() || 'Unknown'}
                </span>
                <span className="text-sm text-gray-600 dark:text-gray-300">
                  {formatFileSize(file.size) || 'Size unknown'}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-gray-500">
                  {file.indexer_display_name || 'Unknown source'}
                </span>
                {file.downloadable_by_me && (
                  <button
                    type="button"
                    className="hover-action cursor-pointer rounded-md px-2 py-1 text-sm font-medium text-sky-700 dark:text-sky-300"
                    onClick={() => onDownload(file)}
                  >
                    Download
                  </button>
                )}
              </div>
            ))}
          </div>
          <aside className="rounded-lg border border-(--border-muted) px-4 py-4">
            <h3 className="text-sm font-semibold text-(--text)">Send to Kindle</h3>
            <p className="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">
              {selectedKindleFormat
                ? `Selected format: ${selectedKindleFormat.toUpperCase()}. EPUB is selected by default.`
                : 'No Kindle-compatible EPUB file is available.'}
            </p>
            <select
              value={selectedKindleFormat ?? 'auto'}
              disabled={!kindleFormats.length}
              onChange={(event) => setKindleFormat(event.target.value)}
              className="mt-4 w-full rounded-md border border-(--border-muted) bg-transparent px-2 py-2 text-sm text-(--text)"
            >
              <option value="auto">Auto (EPUB)</option>
              {kindleFormats.map((format) => (
                <option key={format} value={format}>
                  {format.toUpperCase()}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={!selectedKindleFormat}
              className={`mt-2 w-full rounded-md border border-(--border-muted) px-3 py-2 text-sm font-medium text-(--text) disabled:cursor-not-allowed disabled:opacity-50 ${selectedKindleFormat ? 'hover-action cursor-pointer' : ''}`}
              onClick={() => selectedKindleFormat && onSendToKindle(selectedKindleFormat)}
            >
              Send {selectedKindleFormat?.toUpperCase() ?? 'file'} to Kindle
            </button>
            <button
              type="button"
              className="hover-action mt-3 cursor-pointer rounded-md px-2 py-1 text-xs text-emerald-700 underline dark:text-emerald-300"
              onClick={onOpenSettings}
            >
              Configure Kindle email in Settings
            </button>
          </aside>
        </div>
      ) : (
        <div className="mt-4 rounded-lg bg-(--bg-soft) px-4 py-4 text-sm text-gray-600 dark:text-gray-300">
          {book.in_flight.length ? 'A release is downloading.' : 'No files are available yet.'}
        </div>
      )}
      <details className="mt-6">
        <summary className="cursor-pointer text-sm font-medium text-gray-600 dark:text-gray-300">
          Advanced: show all releases{releases.length ? ` (${releases.length})` : ''}
        </summary>
        {releases.length > 0 && (
          <div className="mt-3 space-y-2 border-l border-(--border-muted) pl-4">
            {releases.map(([taskId, files]) => (
              <div key={taskId} className="rounded-lg bg-(--bg-soft) px-4 py-3">
                <div className="flex items-center gap-3">
                  <p className="min-w-0 flex-1 text-sm font-medium text-(--text)">
                    {files[0].indexer_display_name || 'Unknown source'}
                  </p>
                  {files.some((file) => file.downloadable_by_me) && (
                    <button
                      type="button"
                      className="hover-action cursor-pointer rounded-md px-2 py-1 text-xs font-medium text-rose-700 dark:text-rose-300"
                      onClick={() => onUnlinkRelease(files[0])}
                    >
                      Unlink release
                    </button>
                  )}
                </div>
                <p className="mt-1 text-xs text-gray-500">
                  {files.length} file{files.length === 1 ? '' : 's'} in this release · Grabbed{' '}
                  {dateLabel(files[0].downloaded_at)}
                  {files[0].protocol && ` · ${files[0].protocol}`}
                </p>
                {files.map((file) => (
                  <div key={file.history_id} className="flex items-center gap-3 pt-3 text-sm">
                    <span className="font-medium text-(--text)">
                      {file.format?.toUpperCase() || 'Unknown'}
                    </span>
                    <span className="text-xs text-gray-500">
                      {formatFileSize(file.size) || 'Size unknown'}
                    </span>
                    {file.downloadable_by_me && (
                      <button
                        type="button"
                        className="hover-action ml-auto cursor-pointer rounded-md px-2 py-1 text-sm font-medium text-sky-700 dark:text-sky-300"
                        onClick={() => onDownload(file)}
                      >
                        Download
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </details>
    </section>
  );
};

export const BookDetailPage = ({
  autoFindReleases,
  canFindReleases,
  isRequestOnly,
  onFindReleases,
  onOpenSettings,
  onShowToast,
}: BookDetailPageProps) => {
  const { bookId: rawBookId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const bookId = Number(rawBookId);
  const [book, setBook] = useState<BookDetailResponse | null>(null);
  const [request, setRequest] = useState<RequestRecord>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [autoOpenedFor, setAutoOpenedFor] = useState<number | null>(null);
  const firstAddIntent = hasAutoFindReleasesIntent(location.state);

  const load = useCallback(async () => {
    if (!Number.isInteger(bookId) || bookId < 1) {
      setError('Not in your library');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [detail, requests] = await Promise.all([
        getLibraryBook(bookId),
        isRequestOnly ? listLibraryRequests() : Promise.resolve([]),
      ]);
      setBook(detail);
      setRequest(requests.find((entry) => Number(entry.book_id) === bookId));
    } catch (caught) {
      if (isApiResponseError(caught) && (caught.status === 403 || caught.status === 404)) {
        setError('Not in your library');
      } else {
        setError(caught instanceof Error ? caught.message : 'Failed to load this book');
      }
    } finally {
      setLoading(false);
    }
  }, [bookId, isRequestOnly]);

  useDependencyEffect(() => {
    void load();
  }, [load]);

  useDependencyEffect(() => {
    if (
      book &&
      shouldAutoFindReleases({
        canFindReleases,
        autoFindReleases,
        firstAddIntent,
        hasFiles: book.files.length > 0,
        hasInFlight: book.in_flight.length > 0,
        alreadyOpened: autoOpenedFor === book.book_id,
      })
    ) {
      setAutoOpenedFor(book.book_id);
      void navigate(location.pathname, { replace: true, state: null });
      onFindReleases(toReleaseBook(book));
    }
  }, [
    autoFindReleases,
    autoOpenedFor,
    book,
    canFindReleases,
    firstAddIntent,
    location.pathname,
    navigate,
    onFindReleases,
  ]);

  const mutate = async (action: () => Promise<void>, success: string) => {
    try {
      await action();
      onShowToast(success, 'success');
      await load();
    } catch (caught) {
      onShowToast(caught instanceof Error ? caught.message : 'Action failed', 'error');
    }
  };

  if (loading) return <BookDetailSkeleton />;
  if (error) {
    const unavailable = error === 'Not in your library';
    return (
      <section className="mx-auto max-w-5xl px-4 py-10 text-center sm:px-6 lg:px-8">
        <h1 className="text-xl font-semibold text-(--text)">{error}</h1>
        <button
          type="button"
          className="hover-action mt-4 cursor-pointer rounded-md border border-(--border-muted) px-3 py-2 text-sm"
          onClick={() => (unavailable ? void navigate('/library') : void load())}
        >
          {unavailable ? 'Back to library' : 'Retry'}
        </button>
      </section>
    );
  }
  if (!book) return null;

  const metadata = [
    book.publish_year,
    book.series_name &&
      `${book.series_name}${book.series_position ? ` #${book.series_position}` : ''}`,
    book.language?.toUpperCase(),
    book.isbn_13 && `ISBN ${book.isbn_13}`,
  ].filter(Boolean);

  return (
    <section className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-10">
      <header className="flex gap-5 border-b border-(--border-muted) pb-8">
        {book.cover_url ? (
          <img
            src={withBasePath(book.cover_url)}
            alt={`Cover of ${book.title ?? 'book'}`}
            className="h-52 w-36 rounded-lg object-cover shadow-lg"
          />
        ) : (
          <div className="flex h-52 w-36 items-center justify-center rounded-lg bg-(--bg-soft) text-xs text-gray-500">
            No cover
          </div>
        )}
        <div className="min-w-0 self-end">
          <p className="text-xs font-semibold tracking-[0.16em] text-emerald-700 uppercase dark:text-emerald-300">
            {bookMembershipLabel(book.in_my_library)}
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-(--text)">{book.title}</h1>
          {book.subtitle && (
            <p className="mt-1 text-lg text-gray-600 dark:text-gray-300">{book.subtitle}</p>
          )}
          <p className="mt-3 text-sm font-medium text-gray-700 dark:text-gray-200">
            {book.author || 'Unknown author'}
          </p>
          {metadata.length > 0 && (
            <p className="mt-3 text-xs text-gray-500">{metadata.join(' · ')}</p>
          )}
        </div>
      </header>
      {book.metadata_json?.display_fields?.length ? (
        <dl className="mt-6 flex flex-wrap gap-3">
          {book.metadata_json.display_fields.slice(0, 3).map((field) => (
            <div key={field.label} className="rounded-lg bg-(--bg-soft) px-3 py-2">
              <dt className="text-xs text-gray-500">{field.label}</dt>
              <dd className="mt-0.5 text-sm font-semibold text-(--text)">{field.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {isRequestOnly && book.files.length === 0 ? (
        <section className="mt-10">
          <h2 className="font-semibold text-(--text)">Availability</h2>
          <RequestState
            request={request}
            onRequest={() =>
              void mutate(async () => {
                await createLibraryRequest(book.book_id);
              }, 'Book requested')
            }
            onCancel={() =>
              request &&
              void mutate(async () => {
                await cancelRequest(request.id);
              }, 'Request cancelled')
            }
          />
        </section>
      ) : (
        <AvailableFiles
          book={book}
          canFindReleases={canFindReleases}
          onDownload={(file) =>
            void mutate(
              () => downloadLibraryFile(book.book_id, { historyId: file.history_id }),
              'Download started',
            )
          }
          onFindReleases={() => onFindReleases(toReleaseBook(book))}
          onOpenSettings={onOpenSettings}
          onSendToKindle={(format) =>
            void mutate(async () => {
              await sendLibraryBookToKindle(book.book_id, format);
            }, 'Sent to Kindle')
          }
          onUnlinkRelease={(file) =>
            void mutate(
              () => unlinkLibraryRelease(book.book_id, file.history_id),
              'Release unlinked',
            )
          }
        />
      )}
      <article className="mt-10 max-w-4xl border-t border-(--border-muted) pt-6">
        <h2 className="text-sm font-semibold text-(--text)">About this book</h2>
        <p className="mt-3 leading-7 whitespace-pre-line text-gray-700 dark:text-gray-200">
          {book.metadata_json?.description ||
            "No description is available from this book's metadata provider."}
        </p>
      </article>
    </section>
  );
};

const BookDetailSkeleton = () => (
  <section className="mx-auto max-w-7xl animate-pulse px-4 py-8 sm:px-6 lg:px-10">
    <div className="h-52 w-36 rounded bg-gray-200 dark:bg-gray-700" />
    <div className="mt-6 h-20 rounded-xl bg-gray-200 dark:bg-gray-700" />
  </section>
);
