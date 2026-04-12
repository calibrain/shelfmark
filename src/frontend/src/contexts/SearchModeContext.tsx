import type { ReactNode } from 'react';
import { createContext, useContext, useMemo } from 'react';

import type { SearchMode } from '../types';

interface SearchModeContextValue {
  searchMode: SearchMode;
  isUniversalMode: boolean;
}

const SearchModeContext = createContext<SearchModeContextValue | null>(null);

export function useSearchMode(): SearchModeContextValue {
  const ctx = useContext(SearchModeContext);
  if (!ctx) {
    throw new Error('useSearchMode must be used within SearchModeProvider');
  }
  return ctx;
}

interface SearchModeProviderProps {
  searchMode: SearchMode;
  children: ReactNode;
}

export function SearchModeProvider({ searchMode, children }: SearchModeProviderProps) {
  const value = useMemo(
    () => ({ searchMode, isUniversalMode: searchMode === 'universal' }),
    [searchMode],
  );

  return <SearchModeContext.Provider value={value}>{children}</SearchModeContext.Provider>;
}
