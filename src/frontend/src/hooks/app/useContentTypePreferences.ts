import { useCallback, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { ContentType } from '../../types';
import { useDependencyEffect } from '../useMountEffect';

const CONTENT_TYPE_STORAGE_KEY = 'preferred-content-type';

interface ContentTypePreference {
  contentType: ContentType;
  combinedMode: boolean;
}

const readInitialPreference = (): ContentTypePreference => {
  try {
    const saved = localStorage.getItem(CONTENT_TYPE_STORAGE_KEY);
    if (saved === 'combined') {
      return { contentType: 'ebook', combinedMode: true };
    }
    if (saved === 'ebook' || saved === 'audiobook') {
      return { contentType: saved, combinedMode: false };
    }
  } catch {
    // localStorage may be unavailable in private browsing
  }
  return { contentType: 'ebook', combinedMode: false };
};

export const useContentTypePreferences = (): {
  contentType: ContentType;
  setContentType: Dispatch<SetStateAction<ContentType>>;
  combinedMode: boolean;
  setCombinedMode: Dispatch<SetStateAction<boolean>>;
} => {
  // Both values live in one state object so each setter can derive the other
  // from a pure updater instead of mirroring it into a ref during render.
  const [preference, setPreference] = useState<ContentTypePreference>(readInitialPreference);
  const { contentType, combinedMode } = preference;

  const setContentType: Dispatch<SetStateAction<ContentType>> = useCallback((value) => {
    setPreference((current) => ({
      ...current,
      contentType: typeof value === 'function' ? value(current.contentType) : value,
    }));
  }, []);

  const setCombinedMode: Dispatch<SetStateAction<boolean>> = useCallback((value) => {
    setPreference((current) => ({
      ...current,
      combinedMode: typeof value === 'function' ? value(current.combinedMode) : value,
    }));
  }, []);

  useDependencyEffect(() => {
    try {
      localStorage.setItem(CONTENT_TYPE_STORAGE_KEY, combinedMode ? 'combined' : contentType);
    } catch {
      // localStorage may be unavailable in private browsing
    }
  }, [contentType, combinedMode]);

  return {
    contentType,
    setContentType,
    combinedMode,
    setCombinedMode,
  };
};
