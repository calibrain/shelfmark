// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LibraryPage } from '../library/LibraryPage';

const renderPage = (isAdmin: boolean) =>
  render(
    <MemoryRouter>
      <LibraryPage isAdmin={isAdmin} />
    </MemoryRouter>,
  );

describe('Library page scope', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
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
      return Promise.resolve(new Response(JSON.stringify({ books })));
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
    expect(screen.getByText(/1 total work, 1 waiting to be found/)).not.toBeNull();
    expect(allBooks.getAttribute('aria-pressed')).toBe('true');
    expect(fetch).toHaveBeenCalledWith('/api/library/books?scope=all', expect.any(Object));

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
});
