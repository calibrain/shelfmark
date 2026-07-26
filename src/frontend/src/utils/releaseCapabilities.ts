import type { LibraryCapability } from '../types';

export const canUseManualReleaseQuery = (isAdmin: boolean): boolean => isAdmin;

export const isRequestOnlyLibraryUser = (
  isAdmin: boolean,
  libraryCapability: LibraryCapability | null,
): boolean => !isAdmin && libraryCapability === 'request-only';
