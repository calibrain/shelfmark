import { useEffect, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { ContentType } from '../../types';

const CONTENT_TYPE_STORAGE_KEY = 'preferred-content-type';

const readInitialPreference = (): { contentType: ContentType; combinedMode: boolean } => {
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
  const initialPreference = readInitialPreference();
  const [contentType, setContentType] = useState<ContentType>(() => initialPreference.contentType);
  const [combinedMode, setCombinedMode] = useState<boolean>(() => initialPreference.combinedMode);

  useEffect(() => {
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
