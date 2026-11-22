import type { Book } from '../types';

// A synthetic "manual" book: the release modal searches the sources with the user's own words.
export const buildManualSearchBook = (query: string, now: number = Date.now()): Book | null => {
  const trimmed = query.trim();
  if (!trimmed) return null;
  const manualId = `manual_${now}`;
  return {
    id: manualId,
    title: trimmed,
    author: '',
    provider: 'manual',
    provider_id: manualId,
    search_title: trimmed,
  };
};
