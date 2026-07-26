import { useState } from 'react';

import { searchMetadata } from '../services/api';
import type { Book } from '../types';

interface BookAddModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAdd: (book: Book) => Promise<void>;
}

export const BookAddModal = ({ isOpen, onClose, onAdd }: BookAddModalProps) => {
  const [query, setQuery] = useState('');
  const [books, setBooks] = useState<Book[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [addingId, setAddingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    setIsSearching(true);
    setError(null);
    try {
      setBooks((await searchMetadata(query.trim())).books);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to search for books');
    } finally {
      setIsSearching(false);
    }
  };

  const add = async (book: Book) => {
    setAddingId(book.id);
    setError(null);
    try {
      await onAdd(book);
      onClose();
    } catch {
      setError('Unable to add this book');
    } finally {
      setAddingId(null);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-[max(4rem,env(safe-area-inset-top))]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-book-title"
    >
      <div
        className="w-full max-w-2xl rounded-xl bg-(--bg) p-5 shadow-2xl"
        style={{ borderColor: 'var(--border-muted)' }}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold tracking-widest text-violet-600 uppercase dark:text-violet-300">
              Library
            </p>
            <h2 id="add-book-title" className="mt-1 text-xl font-semibold">
              Add a book
            </h2>
          </div>
          <button
            type="button"
            className="hover-action rounded-full p-2"
            onClick={onClose}
            aria-label="Close add book"
          >
            &#215;
          </button>
        </div>
        <form className="mt-5 flex gap-2" onSubmit={(event) => void search(event)}>
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Title or author"
            className="min-w-0 flex-1 rounded-md border border-(--border-muted) bg-transparent px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={isSearching}
            className="rounded-md bg-violet-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
          >
            {isSearching ? 'Searching...' : 'Search'}
          </button>
        </form>
        {error && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>}
        <div
          className="mt-4 max-h-[55vh] divide-y overflow-y-auto"
          style={{ borderColor: 'var(--border-muted)' }}
        >
          {books.map((book) => (
            <div key={book.id} className="flex items-center gap-3 py-3">
              {book.preview && (
                <img src={book.preview} alt="" className="h-14 w-10 rounded object-cover" />
              )}
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{book.title || 'Untitled'}</p>
                <p className="truncate text-sm opacity-65">
                  {book.author || 'Unknown author'}
                  {book.year ? ` · ${book.year}` : ''}
                </p>
              </div>
              <button
                type="button"
                disabled={addingId === book.id}
                className="rounded-md border border-violet-500 px-3 py-1.5 text-sm font-semibold text-violet-700 disabled:opacity-60 dark:text-violet-300"
                onClick={() => void add(book)}
              >
                {addingId === book.id ? 'Adding...' : 'Add'}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
