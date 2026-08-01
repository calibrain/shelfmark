interface LibraryPaginationProps {
  currentPage: number;
  pageCount: number;
  pages: Array<number | 'ellipsis'>;
  setPage: (page: number) => void;
}

const buttonClass =
  'inline-flex min-h-9 min-w-9 cursor-pointer items-center justify-center rounded-lg px-3 font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-500 disabled:pointer-events-none disabled:cursor-default disabled:opacity-35';

export const LibraryPagination = ({
  currentPage,
  pageCount,
  pages,
  setPage,
}: LibraryPaginationProps) => (
  <nav className="mt-10 flex items-center justify-center gap-3" aria-label="Library pages">
    <button
      type="button"
      aria-label="Previous"
      disabled={currentPage === 1}
      className={`${buttonClass} border border-(--border-muted) bg-(--surface) text-lg shadow-sm hover:border-violet-400 hover:bg-violet-100 dark:hover:bg-violet-950`}
      onClick={() => setPage(currentPage - 1)}
    >
      &#8249;
    </button>
    <div className="flex items-center gap-1 rounded-xl bg-(--hover-surface) p-1.5 shadow-inner">
      {pages.map((item, index) =>
        item === 'ellipsis' ? (
          <span key={`ellipsis-${pages[index + 1]}`} className="px-1 text-sm opacity-50">
            ...
          </span>
        ) : (
          <button
            key={item}
            type="button"
            aria-current={item === currentPage ? 'page' : undefined}
            className={`${buttonClass} ${
              item === currentPage
                ? 'bg-violet-600 text-white shadow-sm'
                : 'text-(--text) hover:bg-violet-100 hover:text-violet-950 dark:hover:bg-violet-950'
            }`}
            onClick={() => setPage(item)}
          >
            {item}
          </button>
        ),
      )}
    </div>
    <button
      type="button"
      aria-label="Next"
      disabled={currentPage === pageCount}
      className={`${buttonClass} border border-(--border-muted) bg-(--surface) text-lg shadow-sm hover:border-violet-400 hover:bg-violet-100 dark:hover:bg-violet-950`}
      onClick={() => setPage(currentPage + 1)}
    >
      &#8250;
    </button>
  </nav>
);
