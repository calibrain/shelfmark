import { useEffect } from 'react';
import { Book, WishlistItem } from '../types';

interface WishlistSidebarProps {
  isOpen: boolean;
  onClose: () => void;
  items: WishlistItem[];
  isLoading: boolean;
  onSearch: (book: Book) => void;
  onRemove: (bookId: string) => void;
}

export function WishlistSidebar({
  isOpen,
  onClose,
  items,
  isLoading,
  onSearch,
  onRemove,
}: WishlistSidebarProps) {
  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  return (
    <>
      {/* Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/20 backdrop-blur-sm lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed top-0 right-0 h-full w-full sm:w-96 z-50 flex flex-col shadow-2xl transition-transform duration-300 ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ background: 'var(--bg)' }}
        aria-label="Wishlist"
        aria-hidden={!isOpen}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-4 py-3 border-b flex-shrink-0"
          style={{ borderColor: 'var(--border-muted)' }}
        >
          <div className="flex items-center gap-2">
            <svg
              className="w-5 h-5"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0 1 11.186 0Z"
              />
            </svg>
            <span className="font-semibold text-sm">Wishlist</span>
            {items.length > 0 && (
              <span
                className="text-xs font-medium px-1.5 py-0.5 rounded-full"
                style={{ background: 'var(--hover-surface)', color: 'var(--text)' }}
              >
                {items.length}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-full hover-action transition-colors"
            aria-label="Close wishlist"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto py-2">
          {isLoading ? (
            <div className="flex items-center justify-center h-32 opacity-60 text-sm">
              Loading...
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-3 opacity-60 text-sm px-6 text-center">
              <svg
                className="w-10 h-10 opacity-40"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth="1.2"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0 1 11.186 0Z"
                />
              </svg>
              <p>No books saved yet.</p>
              <p className="text-xs opacity-70">
                Bookmark a book from search results to save it here for later.
              </p>
            </div>
          ) : (
            <ul className="divide-y" style={{ borderColor: 'var(--border-muted)' }}>
              {items.map((item) => (
                <WishlistItemRow
                  key={item.book_id}
                  item={item}
                  onSearch={onSearch}
                  onRemove={onRemove}
                />
              ))}
            </ul>
          )}
        </div>
      </aside>
    </>
  );
}

interface WishlistItemRowProps {
  item: WishlistItem;
  onSearch: (book: Book) => void;
  onRemove: (bookId: string) => void;
}

function WishlistItemRow({ item, onSearch, onRemove }: WishlistItemRowProps) {
  const book = item.book_data;

  return (
    <li className="flex gap-3 px-4 py-3 hover-surface transition-colors">
      {/* Cover thumbnail */}
      <div
        className="flex-shrink-0 w-10 h-14 rounded overflow-hidden"
        style={{ background: 'var(--border-muted)' }}
      >
        {book.preview ? (
          <img
            src={book.preview}
            alt={book.title}
            className="w-full h-full object-cover object-top"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = 'none';
            }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <svg className="w-4 h-4 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
            </svg>
          </div>
        )}
      </div>

      {/* Book info */}
      <div className="flex-1 min-w-0 flex flex-col justify-between gap-1">
        <div className="min-w-0">
          <p className="text-sm font-medium leading-tight line-clamp-2" title={book.title}>
            {book.title || 'Untitled'}
          </p>
          <p className="text-xs opacity-60 truncate mt-0.5">
            {book.author || 'Unknown author'}
            {book.year ? ` · ${book.year}` : ''}
          </p>
        </div>

        <button
          onClick={() => onSearch(book)}
          className="self-start flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors bg-sky-600 hover:bg-sky-700 text-white"
        >
          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
          </svg>
          Search
        </button>
      </div>

      {/* Remove button */}
      <button
        onClick={() => onRemove(item.book_id)}
        className="flex-shrink-0 p-1.5 rounded-full hover-action transition-colors opacity-50 hover:opacity-100 self-start mt-0.5"
        aria-label={`Remove ${book.title} from wishlist`}
        title="Remove from wishlist"
      >
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </li>
  );
}
