import { useRef } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { Book } from '../../types';
import { onBookTargetChange } from '../../utils/bookTargetEvents';
import { useMountEffect } from '../useMountEffect';

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

  useMountEffect(() => {
    return onBookTargetChange((event) => {
      if (event.selected) return;
      const currentValue = activeListValueRef.current;
      if (!currentValue || String(currentValue) !== event.target) return;
      setBooks((prev) => prev.filter((book) => book.provider_id !== event.bookId));
    });
  });
};
