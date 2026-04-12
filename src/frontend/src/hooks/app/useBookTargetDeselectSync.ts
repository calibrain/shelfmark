import { useEffect, useRef } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { Book } from '../../types';
import { onBookTargetChange } from '../../utils/bookTargetEvents';

interface UseBookTargetDeselectSyncOptions {
  activeListValue: string | number | boolean | null | undefined;
  setBooks: Dispatch<SetStateAction<Book[]>>;
}

export const useBookTargetDeselectSync = ({
  activeListValue,
  setBooks,
}: UseBookTargetDeselectSyncOptions): void => {
  const activeListValueRef = useRef(activeListValue);
  activeListValueRef.current = activeListValue;

  useEffect(() => {
    return onBookTargetChange((event) => {
      if (event.selected) return;
      const currentValue = activeListValueRef.current;
      if (!currentValue || String(currentValue) !== event.target) return;
      setBooks((prev) => prev.filter((book) => book.provider_id !== event.bookId));
    });
  }, [setBooks]);
};
