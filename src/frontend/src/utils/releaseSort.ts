import { Release } from '../types';

export interface SortState {
  key: string;
  direction: 'asc' | 'desc';
}

// LocalStorage helpers for persisting sort preferences per source
const SORT_STORAGE_PREFIX = 'cwa-bd-release-sort-';

export function getSavedSort(sourceName: string): SortState | null {
  try {
    const saved = localStorage.getItem(`${SORT_STORAGE_PREFIX}${sourceName}`);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (parsed.key && parsed.direction) {
        return parsed as SortState;
      }
    }
    return null;
  } catch {
    return null;
  }
}

export function saveSort(sourceName: string, sortState: SortState): void {
  try {
    localStorage.setItem(`${SORT_STORAGE_PREFIX}${sourceName}`, JSON.stringify(sortState));
  } catch {
    // localStorage may be unavailable in private browsing
  }
}

export function clearSort(sourceName: string): void {
  try {
    localStorage.removeItem(`${SORT_STORAGE_PREFIX}${sourceName}`);
  } catch {
    // localStorage may be unavailable in private browsing
  }
}

// Get nested value from an object using dot notation path
function getNestedSortValue(obj: Record<string, unknown>, path: string): unknown {
  return path.split('.').reduce<unknown>((current, key) => {
    if (current && typeof current === 'object' && key in (current as Record<string, unknown>)) {
      return (current as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj);
}

// Infer default sort direction from column render type
export function inferDefaultDirection(renderType: string): 'asc' | 'desc' {
  // Numeric types sort descending by default (bigger is usually better)
  if (renderType === 'size' || renderType === 'number' || renderType === 'peers') {
    return 'desc';
  }
  // Text/badge types sort ascending (alphabetical)
  return 'asc';
}

// Sort releases by a column
export function sortReleases(
  releases: Release[],
  sortKey: string,
  direction: 'asc' | 'desc'
): Release[] {
  return [...releases].sort((a, b) => {
    const aVal = getNestedSortValue(a as unknown as Record<string, unknown>, sortKey);
    const bVal = getNestedSortValue(b as unknown as Record<string, unknown>, sortKey);

    // Handle null/undefined - sort them to the end
    if (aVal == null && bVal == null) return 0;
    if (aVal == null) return 1;
    if (bVal == null) return -1;

    // Numeric comparison
    if (typeof aVal === 'number' && typeof bVal === 'number') {
      return direction === 'asc' ? aVal - bVal : bVal - aVal;
    }

    // String comparison (case-insensitive)
    const aStr = String(aVal).toLowerCase();
    const bStr = String(bVal).toLowerCase();
    const cmp = aStr.localeCompare(bStr);
    return direction === 'asc' ? cmp : -cmp;
  });
}
