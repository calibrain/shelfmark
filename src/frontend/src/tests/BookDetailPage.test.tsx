// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BookDetailPage } from '../library/BookDetailPage';

const socketListeners = vi.hoisted(() => new Map<string, (payload: { book_id: number }) => void>());

vi.mock('../contexts/SocketContext', () => ({
  useSocket: () => ({
    socket: {
      on: (event: string, listener: (payload: { book_id: number }) => void) =>
        socketListeners.set(event, listener),
      off: (event: string) => socketListeners.delete(event),
    },
    connected: true,
  }),
}));

const book = {
  book_id: 1,
  metadata_provider: 'hardcover',
  provider_book_id: 'shared-book',
  title: 'Shared Book',
  author: 'Author',
  subtitle: null,
  publish_year: null,
  isbn_13: null,
  cover_url: null,
  series_name: null,
  series_position: null,
  language: 'en',
  metadata_json: {},
  in_my_library: true,
  files: [
    {
      history_id: 10,
      task_id: 'shared-release',
      format: 'epub',
      size: '1024',
      indexer_display_name: 'Indexer',
      protocol: null,
      downloaded_at: '2026-01-01T00:00:00+00:00',
      downloadable_by_me: true,
    },
  ],
  in_flight: [],
};

describe('BookDetailPage request-only availability', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    socketListeners.clear();
  });

  it('reloads when availability changes for the open book only', async () => {
    let currentBook: typeof book = { ...book, files: [] };
    const fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify(currentBook))));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <MemoryRouter initialEntries={['/library/1']}>
        <Routes>
          <Route
            path="/library/:bookId"
            element={
              <BookDetailPage
                autoFindReleases={false}
                canFindReleases
                canDeleteReleases={false}
                isRequestOnly={false}
                isAdmin={false}
                onFindReleases={() => undefined}
                onOpenSettings={() => undefined}
                onShowToast={() => undefined}
              />
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: 'Shared Book' });
    socketListeners.get('library_book_availability')?.({ book_id: 2 });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    currentBook = book;
    socketListeners.get('library_book_availability')?.({ book_id: 1 });

    expect(
      await screen.findAllByRole('button', { name: 'Download' }).then((buttons) => buttons[0]),
    ).not.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('shows existing file controls without release discovery', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve(new Response(JSON.stringify(url === '/api/library/books/1' ? book : []))),
      ),
    );

    render(
      <MemoryRouter initialEntries={['/library/1']}>
        <Routes>
          <Route
            path="/library/:bookId"
            element={
              <BookDetailPage
                autoFindReleases={false}
                canFindReleases={false}
                canDeleteReleases={false}
                isRequestOnly
                isAdmin={false}
                onFindReleases={() => undefined}
                onOpenSettings={() => undefined}
                onShowToast={() => undefined}
              />
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findAllByRole('button', { name: 'Download' }).then((buttons) => buttons[0]),
    ).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Send EPUB to Kindle' })).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Find another release' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete release' })).toBeNull();
  });

  it('allows a new request after a previous request was cancelled', async () => {
    const unavailableBook = { ...book, files: [] };
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              url === '/api/library/books/1'
                ? unavailableBook
                : [{ id: 9, book_id: 1, status: 'cancelled' }],
            ),
          ),
        ),
      ),
    );

    render(
      <MemoryRouter initialEntries={['/library/1']}>
        <Routes>
          <Route
            path="/library/:bookId"
            element={
              <BookDetailPage
                autoFindReleases={false}
                canFindReleases={false}
                canDeleteReleases={false}
                isRequestOnly
                isAdmin={false}
                onFindReleases={() => undefined}
                onOpenSettings={() => undefined}
                onShowToast={() => undefined}
              />
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole('button', { name: 'Request this book' })).not.toBeNull();
    expect(screen.queryByText('Request cancelled')).toBeNull();
  });
});

describe('BookDetailPage release deletion', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    socketListeners.clear();
  });

  it('removes a release after an admin deletes it', async () => {
    const user = userEvent.setup();
    const deletedBook = {
      ...book,
      files: [],
    };
    let currentBook = book;
    const fetchMock = vi.fn((_url: string, options?: RequestInit) => {
      if (options?.method === 'DELETE') {
        currentBook = deletedBook;
        return Promise.resolve(new Response(JSON.stringify({ status: 'deleted' })));
      }
      return Promise.resolve(new Response(JSON.stringify(currentBook)));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <MemoryRouter initialEntries={['/library/1']}>
        <Routes>
          <Route
            path="/library/:bookId"
            element={
              <BookDetailPage
                autoFindReleases={false}
                canFindReleases
                canDeleteReleases
                isRequestOnly={false}
                isAdmin
                onFindReleases={() => undefined}
                onOpenSettings={() => undefined}
                onShowToast={() => undefined}
              />
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: 'Shared Book' });
    await user.click(screen.getByText('Advanced: show all releases (1)'));
    await user.click(screen.getByRole('button', { name: 'Delete release' }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Delete release' })).toBeNull();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/library/books/1/downloads/10',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('shows the admin purge preview and purges after affirmative opt-in', async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith('/purge-preview')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ users: [{ display_name: 'Alice Reader', username: 'alice' }] }),
          ),
        );
      }
      if (url.endsWith('/purge')) {
        expect(init?.method).toBe('DELETE');
        return Promise.resolve(new Response(JSON.stringify({ status: 'purged' })));
      }
      return Promise.resolve(new Response(JSON.stringify(book)));
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={['/library/1']}>
        <Routes>
          <Route
            path="/library/:bookId"
            element={
              <BookDetailPage
                autoFindReleases={false}
                canFindReleases={true}
                canDeleteReleases
                isRequestOnly={false}
                isAdmin
                onFindReleases={() => undefined}
                onOpenSettings={() => undefined}
                onShowToast={() => undefined}
              />
            }
          />
          <Route path="/library" element={<p>Library</p>} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: 'Delete book' }));
    await user.click(screen.getByRole('checkbox', { name: 'Delete for all users' }));
    expect(await screen.findByText('Alice Reader')).not.toBeNull();
    await user.click(screen.getByRole('button', { name: 'Delete for all users' }));

    expect(await screen.findByText('Library')).not.toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/library/books/1/purge',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });
});
