import { useEffectEvent } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { Book } from '../../types';
import { onBookTargetChange, type BookTargetChangeEvent } from '../../utils/bookTargetEvents';
import { useMountEffect } from '../useMountEffect';

interface UseBookTargetDeselectSyncOptions {
  activeListValue: string | number | boolean | null | undefined;
  setBooks: Dispatch<SetStateAction<Book[]>>;
}

export const useBookTargetDeselectSync = ({
  activeListValue,
  setBooks,
}: UseBookTargetDeselectSyncOptions): void => {
  const handleTargetChange = useEffectEvent((event: BookTargetChangeEvent) => {
    if (event.selected) return;
    if (!activeListValue || String(activeListValue) !== event.target) return;
    setBooks((prev) => prev.filter((book) => book.provider_id !== event.bookId));
  });

  useMountEffect(() => onBookTargetChange(handleTargetChange));
};
