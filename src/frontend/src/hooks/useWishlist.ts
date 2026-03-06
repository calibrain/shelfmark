import { useCallback, useEffect, useRef, useState } from 'react';
import { Book, WishlistItem } from '../types';
import { addToWishlist, listWishlist, removeFromWishlist } from '../services/api';

interface UseWishlistOptions {
  enabled: boolean;
}

export interface UseWishlistReturn {
  items: WishlistItem[];
  isLoading: boolean;
  isInWishlist: (bookId: string) => boolean;
  toggle: (book: Book) => Promise<void>;
  refresh: () => Promise<void>;
}

export function useWishlist({ enabled }: UseWishlistOptions): UseWishlistReturn {
  const [items, setItems] = useState<WishlistItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const loadedRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setIsLoading(true);
    try {
      const data = await listWishlist();
      setItems(data);
      loadedRef.current = true;
    } catch {
      // Silently fail — wishlist is non-critical
    } finally {
      setIsLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (enabled && !loadedRef.current) {
      void refresh();
    }
    if (!enabled) {
      setItems([]);
      loadedRef.current = false;
    }
  }, [enabled, refresh]);

  const isInWishlist = useCallback(
    (bookId: string) => items.some((item) => item.book_id === bookId),
    [items]
  );

  const toggle = useCallback(
    async (book: Book) => {
      if (isInWishlist(book.id)) {
        // Optimistic removal
        setItems((prev) => prev.filter((item) => item.book_id !== book.id));
        try {
          await removeFromWishlist(book.id);
        } catch {
          // Revert on failure
          void refresh();
        }
      } else {
        // Optimistic add
        const optimistic: WishlistItem = {
          book_id: book.id,
          book_data: book,
          added_at: new Date().toISOString(),
        };
        setItems((prev) => [optimistic, ...prev]);
        try {
          const saved = await addToWishlist(book);
          setItems((prev) =>
            prev.map((item) => (item.book_id === book.id ? saved : item))
          );
        } catch {
          // Revert on failure
          void refresh();
        }
      }
    },
    [isInWishlist, refresh]
  );

  return { items, isLoading, isInWishlist, toggle, refresh };
}
