import { useEffectEvent, useRef, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';

import { useSocket } from '../contexts/SocketContext';
import { useDependencyEffect } from '../hooks/useMountEffect';
import { getLibraryBooks } from '../services/api';
import { withBasePath } from '../utils/basePath';
import type { LibraryBookSummary } from './types';

type FileFilter = 'all' | 'with-files' | 'needs-files';
type LibraryScope = 'mine' | 'all';

const getFileFilter = (value: string | null): FileFilter => {
  if (value === 'with-files' || value === 'needs-files') return value;
  return 'all';
};

const getScope = (value: string | null, isAdmin: boolean): LibraryScope =>
  isAdmin && value === 'all' ? 'all' : 'mine';

const matchesFilter = (book: LibraryBookSummary, filter: FileFilter): boolean => {
  if (filter === 'with-files') return book.formats_on_disk.length > 0;
  if (filter === 'needs-files') return book.formats_on_disk.length === 0;
  return true;
};

const Cover = ({ book }: { book: LibraryBookSummary }) => {
  const [imageFailed, setImageFailed] = useState(false);
  const initial = (book.title?.trim()[0] ?? '?').toUpperCase();

  if (!book.cover_url || imageFailed) {
    return (
      <div className="flex aspect-[2/3] w-full items-center justify-center rounded-lg bg-linear-to-br from-slate-700 to-slate-950 text-4xl font-semibold text-slate-100 shadow-sm">
        {initial}
      </div>
    );
  }

  return (
    <img
      src={withBasePath(book.cover_url)}
      alt={`Cover of ${book.title ?? 'untitled book'}`}
      className="aspect-[2/3] w-full rounded-lg object-cover shadow-sm transition duration-200 group-hover:-translate-y-1 group-hover:shadow-lg"
      onError={() => setImageFailed(true)}
    />
  );
};

const FormatBadges = ({ formats }: { formats: LibraryBookSummary['formats_on_disk'] }) => {
  const uniqueFormats = [...new Set(formats.flatMap(({ format }) => (format ? [format] : [])))];
  if (!uniqueFormats.length) return null;

  return (
    <div className="flex flex-wrap gap-1">
      {uniqueFormats.map((format) => (
        <span
          key={format}
          className="rounded bg-(--hover-surface) px-1.5 py-0.5 text-[10px] font-bold tracking-wide"
        >
          {format.toUpperCase()}
        </span>
      ))}
    </div>
  );
};

export const LibraryPage = ({ isAdmin }: { isAdmin: boolean }) => {
  const { socket } = useSocket();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [books, setBooks] = useState<LibraryBookSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const latestRequest = useRef(0);
  const query = searchParams.get('q') ?? '';
  const filter = getFileFilter(searchParams.get('availability'));
  const scope = getScope(searchParams.get('scope'), isAdmin);

  const updateParam = (name: string, value: string, defaultValue: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (value === defaultValue) next.delete(name);
      else next.set(name, value);
      return next;
    });
  };

  const load = async () => {
    const requestId = ++latestRequest.current;
    setLoading(true);
    setError(null);
    try {
      const response = await getLibraryBooks({ scope, query });
      if (requestId === latestRequest.current) setBooks(response.books);
    } catch (caught) {
      if (requestId === latestRequest.current) {
        setError(caught instanceof Error ? caught.message : 'Failed to load your library');
      }
    } finally {
      if (requestId === latestRequest.current) setLoading(false);
    }
  };

  useDependencyEffect(() => {
    void load();
  }, [scope, query]);

  const onAvailability = useEffectEvent(() => {
    void load();
  });

  useDependencyEffect(() => {
    socket?.on('library_book_availability', onAvailability);
    return () => {
      socket?.off('library_book_availability', onAvailability);
    };
  }, [socket]);

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleBooks = books.filter((book) => {
    const searchText = `${book.title ?? ''} ${book.author ?? ''}`.toLocaleLowerCase();
    return (
      matchesFilter(book, filter) && (!normalizedQuery || searchText.includes(normalizedQuery))
    );
  });
  const missingFiles = books.filter((book) => !book.formats_on_disk.length).length;

  return (
    <section className="pb-16">
      <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-semibold tracking-widest text-violet-600 uppercase dark:text-violet-300">
            Library
          </p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-(--text)">
            {scope === 'all' ? "All users' books" : 'Your books'}
          </h1>
          <p className="mt-2 text-sm opacity-65">
            {books.length} {scope === 'all' ? 'total' : 'saved'}{' '}
            {books.length === 1 ? 'work' : 'works'}
            {missingFiles ? `, ${missingFiles} waiting to be found.` : '.'}
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:items-end">
          {isAdmin && (
            <div className="flex rounded-md border border-(--border-muted) p-0.5 text-xs">
              <button
                type="button"
                aria-pressed={scope === 'mine'}
                className={`rounded px-2.5 py-1.5 ${
                  scope === 'mine' ? 'bg-(--hover-surface) font-semibold' : 'opacity-65'
                }`}
                onClick={() => updateParam('scope', 'mine', 'mine')}
              >
                Your books
              </button>
              <button
                type="button"
                aria-pressed={scope === 'all'}
                className={`rounded px-2.5 py-1.5 ${
                  scope === 'all' ? 'bg-(--hover-surface) font-semibold' : 'opacity-65'
                }`}
                onClick={() => updateParam('scope', 'all', 'mine')}
              >
                Show all users' books
              </button>
            </div>
          )}
          <input
            aria-label="Search library"
            value={query}
            onChange={(event) => updateParam('q', event.target.value, '')}
            placeholder="Search title or author"
            className="w-full rounded-md border border-(--border-muted) bg-transparent px-3 py-2 text-sm sm:w-56"
          />
          <div className="flex rounded-md border border-(--border-muted) p-0.5 text-xs">
            {(
              [
                ['all', 'All'],
                ['with-files', 'Has files'],
                ['needs-files', 'Needs files'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={filter === value}
                className={`rounded px-2.5 py-1.5 ${
                  filter === value ? 'bg-(--hover-surface) font-semibold' : 'opacity-65'
                }`}
                onClick={() => updateParam('availability', value, 'all')}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {loading && <LibrarySkeleton />}
      {error && (
        <div className="rounded-xl border border-(--border-muted) p-6 text-center">
          <p className="text-sm text-(--text)">{error}</p>
          <button
            type="button"
            className="mt-3 text-sm text-emerald-700 underline"
            onClick={() => void load()}
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && !books.length && (
        <div className="rounded-xl border border-dashed border-(--border-muted) p-8 text-center">
          <h2 className="font-semibold text-(--text)">
            {scope === 'all' ? "No users' books yet" : 'Your library is empty'}
          </h2>
          <p className="mt-2 text-sm opacity-65">
            {scope === 'all'
              ? 'Books added by any user will appear here.'
              : 'Find a book in search, then add it to your library.'}
          </p>
        </div>
      )}
      {!loading && !error && books.length > 0 && !visibleBooks.length && (
        <div className="rounded-xl border border-dashed border-(--border-muted) p-8 text-center text-sm opacity-65">
          No books match this search and filter.
        </div>
      )}
      {!loading && !error && visibleBooks.length > 0 && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-8 sm:grid-cols-3 lg:grid-cols-5">
          {visibleBooks.map((book) => (
            <article key={book.book_id} className="group min-w-0">
              <Link to={`/library/${book.book_id}${location.search}`} className="block">
                <Cover book={book} />
                <h2 className="mt-3 truncate font-semibold text-(--text)">
                  {book.title ?? 'Untitled'}
                </h2>
                <p className="truncate text-sm opacity-65">{book.author || 'Unknown author'}</p>
              </Link>
              {book.formats_on_disk.length > 0 && (
                <div className="mt-2">
                  <FormatBadges formats={book.formats_on_disk} />
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  );
};

const LibrarySkeleton = () => (
  <div className="grid animate-pulse grid-cols-2 gap-x-4 gap-y-8 sm:grid-cols-3 lg:grid-cols-5">
    {Array.from({ length: 10 }, (_, index) => (
      <div key={index}>
        <div className="aspect-[2/3] rounded-lg bg-gray-200 dark:bg-gray-700" />
        <div className="mt-3 h-4 rounded bg-gray-200 dark:bg-gray-700" />
        <div className="mt-2 h-3 w-2/3 rounded bg-gray-200 dark:bg-gray-700" />
      </div>
    ))}
  </div>
);
