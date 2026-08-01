import { useEffectEvent, useRef, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';

import { useSocket } from '../contexts/SocketContext';
import { useDependencyEffect } from '../hooks/useMountEffect';
import { getLibraryBooks } from '../services/api';
import { withBasePath } from '../utils/basePath';
import { LibraryPagination } from './LibraryPagination';
import type { LibraryBookSummary } from './types';

type FileFilter = 'all' | 'with-files' | 'needs-files';
type LibraryScope = 'mine' | 'all';

const getFileFilter = (value: string | null): FileFilter => {
  if (value === 'with-files' || value === 'needs-files') return value;
  return 'all';
};

const getScope = (value: string | null, isAdmin: boolean): LibraryScope =>
  isAdmin && value === 'all' ? 'all' : 'mine';

const PAGE_SIZE = 25;

const getPage = (value: string | null): number => {
  const page = Number(value);
  return Number.isInteger(page) && page > 0 ? page : 1;
};

const paginationPages = (currentPage: number, pageCount: number): Array<number | 'ellipsis'> => {
  const pages = new Set([1, pageCount]);
  for (let page = currentPage - 2; page <= currentPage + 2; page += 1) {
    if (page > 0 && page <= pageCount) pages.add(page);
  }
  const sorted = [...pages].toSorted((a, b) => a - b);
  return sorted.flatMap((page, index) => {
    const previous = sorted[index - 1];
    return previous && page - previous > 1 ? ['ellipsis', page] : [page];
  });
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
  const [total, setTotal] = useState(0);
  const latestRequest = useRef(0);
  const query = searchParams.get('q') ?? '';
  const filter = getFileFilter(searchParams.get('availability'));
  const scope = getScope(searchParams.get('scope'), isAdmin);
  const page = getPage(searchParams.get('page'));
  const [searchInput, setSearchInput] = useState(query);
  const pageCount = Math.ceil(total / PAGE_SIZE);

  const updateParam = (name: string, value: string, defaultValue: string, resetPage = true) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (value === defaultValue) next.delete(name);
      else next.set(name, value);
      if (resetPage) next.delete('page');
      return next;
    });
  };

  const setPage = (nextPage: number) => updateParam('page', String(nextPage), '1', false);

  useDependencyEffect(() => {
    setSearchInput(query);
  }, [query]);

  useDependencyEffect(() => {
    if (searchInput === query) return undefined;
    const timeout = window.setTimeout(() => updateParam('q', searchInput, '', true), 300);
    return () => window.clearTimeout(timeout);
  }, [query, searchInput]);

  const load = async () => {
    const requestId = ++latestRequest.current;
    setLoading(true);
    setError(null);
    try {
      const response = await getLibraryBooks({
        scope,
        query,
        availability: filter,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      if (requestId === latestRequest.current) {
        const responseTotal = response.total ?? response.books.length;
        const lastPage = Math.max(1, Math.ceil(responseTotal / PAGE_SIZE));
        if (page > lastPage) {
          setSearchParams(
            (current) => {
              const next = new URLSearchParams(current);
              if (lastPage === 1) next.delete('page');
              else next.set('page', String(lastPage));
              return next;
            },
            { replace: true },
          );
          return;
        }
        setBooks(response.books);
        setTotal(responseTotal);
      }
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
  }, [scope, query, filter, page]);

  const onAvailability = useEffectEvent(() => {
    void load();
  });

  useDependencyEffect(() => {
    socket?.on('library_book_availability', onAvailability);
    return () => {
      socket?.off('library_book_availability', onAvailability);
    };
  }, [socket]);

  const firstMatch = total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const lastMatch = Math.min(page * PAGE_SIZE, total);
  const hasFilters = Boolean(query || filter !== 'all');
  const pages = paginationPages(page, pageCount);

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
            Showing {firstMatch}-{lastMatch} of {total} {total === 1 ? 'work' : 'works'}
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
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
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
      {!loading && !error && total === 0 && !hasFilters && (
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
      {!loading && !error && total === 0 && hasFilters && (
        <div className="rounded-xl border border-dashed border-(--border-muted) p-8 text-center text-sm opacity-65">
          No books match this search and filter.
        </div>
      )}
      {!loading && !error && books.length > 0 && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-8 sm:grid-cols-3 lg:grid-cols-5">
          {books.map((book) => (
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
      {!loading && !error && pageCount > 1 && (
        <LibraryPagination
          currentPage={page}
          pageCount={pageCount}
          pages={pages}
          setPage={setPage}
        />
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
