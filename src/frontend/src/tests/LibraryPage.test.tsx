// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LibraryPage } from '../library/LibraryPage';

const socketListeners = vi.hoisted(() => new Map<string, () => void>());

vi.mock('../contexts/SocketContext', () => ({
  useSocket: () => ({
    socket: {
      on: (event: string, listener: () => void) => socketListeners.set(event, listener),
      off: (event: string) => socketListeners.delete(event),
    },
    connected: true,
  }),
}));

const renderPage = (isAdmin: boolean, initialEntry = '/library') =>
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/library" element={<LibraryPage isAdmin={isAdmin} />} />
        <Route path="/library/:bookId" element={<LibraryLocation />} />
      </Routes>
    </MemoryRouter>,
  );

const LibraryLocation = () => {
  const location = useLocation();
  return <p>{location.pathname + location.search}</p>;
};

describe('Library page scope', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    socketListeners.clear();
  });

  it('reloads the active scope after an availability invalidation', async () => {
    let available = false;
    const fetchMock = vi.fn((url: string) => {
      if (url === '/api/library/books?limit=25') {
        return Promise.resolve(new Response(JSON.stringify({ books: [] })));
      }
      const books = available
        ? [
            {
              book_id: 1,
              title: 'Available book',
              author: 'Author',
              cover_url: null,
              formats_on_disk: [{ format: 'epub', size: '1024' }],
              added_at: null,
            },
          ]
        : [];
      expect(url).toBe('/api/library/books?scope=all&limit=25');
      return Promise.resolve(
        new Response(JSON.stringify({ books, total: books.length, limit: 25, offset: 0 })),
      );
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    renderPage(true);
    await user.click(await screen.findByRole('button', { name: "Show all users' books" }));
    await screen.findByRole('heading', { name: "All users' books" });

    available = true;
    socketListeners.get('library_book_availability')?.();

    expect(await screen.findByText('Available book')).not.toBeNull();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });

  it('lets an administrator switch between their books and all users books', async () => {
    const fetch = vi.fn().mockImplementation((url: string) => {
      const books = url.includes('scope=all')
        ? [
            {
              book_id: 1,
              title: 'Shared book',
              author: 'Author',
              cover_url: null,
              formats_on_disk: [],
              added_at: null,
            },
          ]
        : [];
      return Promise.resolve(
        new Response(JSON.stringify({ books, total: books.length, limit: 25, offset: 0 })),
      );
    });
    vi.stubGlobal('fetch', fetch);
    const user = userEvent.setup();

    renderPage(true);

    expect(await screen.findByRole('heading', { name: 'Your books' })).not.toBeNull();
    expect(screen.getByRole('heading', { name: 'Your library is empty' })).not.toBeNull();
    const allBooks = screen.getByRole('button', { name: "Show all users' books" });
    expect(allBooks.getAttribute('aria-pressed')).toBe('false');

    await user.click(allBooks);

    expect(await screen.findByRole('heading', { name: "All users' books" })).not.toBeNull();
    expect(screen.getByText('Showing 1-1 of 1 work')).not.toBeNull();
    expect(allBooks.getAttribute('aria-pressed')).toBe('true');
    expect(fetch).toHaveBeenCalledWith('/api/library/books?scope=all&limit=25', expect.any(Object));

    await user.click(screen.getByRole('button', { name: 'Your books' }));

    expect(await screen.findByRole('heading', { name: 'Your books' })).not.toBeNull();
    expect(screen.getByRole('heading', { name: 'Your library is empty' })).not.toBeNull();
  });

  it('hides the all-users control for non-administrators', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ books: [] }))));

    renderPage(false);

    expect(await screen.findByRole('heading', { name: 'Your books' })).not.toBeNull();
    expect(screen.queryByRole('button', { name: "Show all users' books" })).toBeNull();
  });

  it('derives controls from the URL and preserves filters in detail links', async () => {
    const fetch = vi.fn().mockImplementation(
      () =>
        new Response(
          JSON.stringify({
            books: [
              {
                book_id: 1,
                title: 'Shared book',
                author: 'Author',
                cover_url: null,
                formats_on_disk: [],
                added_at: null,
              },
            ],
            total: 1,
            limit: 25,
            offset: 0,
          }),
        ),
    );
    vi.stubGlobal('fetch', fetch);
    const user = userEvent.setup();

    renderPage(true, '/library?scope=all&availability=needs-files&q=Shared&other=kept');

    expect(await screen.findByRole('heading', { name: "All users' books" })).not.toBeNull();
    expect(screen.getByRole('textbox', { name: 'Search library' }).getAttribute('value')).toBe(
      'Shared',
    );
    expect(screen.getByRole('button', { name: 'Needs files' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(fetch).toHaveBeenCalledWith(
      '/api/library/books?scope=all&q=Shared&availability=needs-files&limit=25',
      expect.any(Object),
    );

    await user.click(screen.getByRole('button', { name: 'All' }));
    await user.click(screen.getByRole('link', { name: /Shared book/i }));

    expect(await screen.findByText('/library/1?scope=all&q=Shared&other=kept')).not.toBeNull();
  });

  it('uses the URL page for API offsets and preserves it in detail links', async () => {
    const fetch = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            books: [
              {
                book_id: 51,
                title: 'Page three book',
                author: 'Author',
                cover_url: null,
                formats_on_disk: [],
                added_at: null,
              },
            ],
            total: 83,
            limit: 25,
            offset: 50,
          }),
        ),
      ),
    );
    vi.stubGlobal('fetch', fetch);

    renderPage(false, '/library?q=dune&availability=with-files&page=3');

    expect(await screen.findByText('Showing 51-75 of 83 works')).not.toBeNull();
    expect(fetch).toHaveBeenCalledWith(
      '/api/library/books?q=dune&availability=with-files&limit=25&offset=50',
      expect.any(Object),
    );
    expect(screen.getByRole('button', { name: '3' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('link', { name: /Page three book/i }).getAttribute('href')).toBe(
      '/library/51?q=dune&availability=with-files&page=3',
    );
  });

  it('debounces search commits and resets the page', async () => {
    const fetch = vi.fn().mockImplementation((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            books: [],
            total: url.includes('q=dune') ? 0 : 100,
            limit: 25,
            offset: 0,
          }),
        ),
      ),
    );
    vi.stubGlobal('fetch', fetch);
    const user = userEvent.setup();

    renderPage(false, '/library?page=3');
    await screen.findByRole('heading', { name: 'Your books' });
    await user.type(screen.getByRole('textbox', { name: 'Search library' }), 'dune');

    expect(fetch).toHaveBeenCalledTimes(1);
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/library/books?q=dune&limit=25',
      expect.any(Object),
    );
  });

  it('clamps a stale bookmarked page to the final page', async () => {
    const fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ books: [], total: 30, limit: 25, offset: 50 })),
        ),
      );
    vi.stubGlobal('fetch', fetch);

    renderPage(false, '/library?page=3');

    await waitFor(() =>
      expect(fetch).toHaveBeenLastCalledWith(
        '/api/library/books?limit=25&offset=25',
        expect.any(Object),
      ),
    );
  });

  it('resets the page immediately when scope or availability changes', async () => {
    const fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ books: [], total: 100, limit: 25, offset: 0 })),
        ),
      );
    vi.stubGlobal('fetch', fetch);
    const user = userEvent.setup();

    renderPage(true, '/library?page=3');
    await screen.findByRole('heading', { name: 'Your books' });
    await user.click(screen.getByRole('button', { name: "Show all users' books" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenLastCalledWith(
        '/api/library/books?scope=all&limit=25',
        expect.any(Object),
      ),
    );

    await user.click(screen.getByRole('button', { name: 'Needs files' }));
    await waitFor(() =>
      expect(fetch).toHaveBeenLastCalledWith(
        '/api/library/books?scope=all&availability=needs-files&limit=25',
        expect.any(Object),
      ),
    );
  });

  it('never renders duplicate cards while repeatedly switching availability filters', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        () =>
          new Response(
            JSON.stringify({
              books: [
                {
                  book_id: 1,
                  title: 'Shared book',
                  author: 'Author',
                  cover_url: null,
                  formats_on_disk: [],
                  added_at: null,
                },
              ],
              total: 1,
              limit: 25,
              offset: 0,
            }),
          ),
      ),
    );
    const user = userEvent.setup();

    renderPage(true, '/library?scope=all');
    await screen.findByText('Shared book');
    await user.click(screen.getByRole('button', { name: 'Needs files' }));
    await user.click(screen.getByRole('button', { name: 'All' }));
    await user.click(screen.getByRole('button', { name: 'Needs files' }));
    await user.click(screen.getByRole('button', { name: 'All' }));
    await user.click(screen.getByRole('button', { name: 'Needs files' }));
    await user.click(screen.getByRole('button', { name: 'All' }));

    expect(screen.getAllByText('Shared book')).toHaveLength(1);
    expect(screen.getByText('Showing 1-1 of 1 work')).not.toBeNull();
    expect(consoleError).not.toHaveBeenCalledWith(expect.stringContaining('same key'));
    consoleError.mockRestore();
  });
});
