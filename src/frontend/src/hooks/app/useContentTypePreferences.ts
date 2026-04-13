import { useCallback, useRef, useState } from 'react';
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
  const [contentType, setContentTypeState] = useState<ContentType>(
    () => initialPreference.contentType,
  );
  const [combinedMode, setCombinedModeState] = useState<boolean>(
    () => initialPreference.combinedMode,
  );
  const contentTypeRef = useRef(contentType);
  const combinedModeRef = useRef(combinedMode);
  contentTypeRef.current = contentType;
  combinedModeRef.current = combinedMode;

  const persistPreference = useCallback(
    (nextContentType: ContentType, nextCombinedMode: boolean) => {
      try {
        localStorage.setItem(
          CONTENT_TYPE_STORAGE_KEY,
          nextCombinedMode ? 'combined' : nextContentType,
        );
      } catch {
        // localStorage may be unavailable in private browsing
      }
    },
    [],
  );

  const setContentType: Dispatch<SetStateAction<ContentType>> = useCallback(
    (value) => {
      setContentTypeState((current) => {
        const nextContentType = typeof value === 'function' ? value(current) : value;
        persistPreference(nextContentType, combinedModeRef.current);
        return nextContentType;
      });
    },
    [persistPreference],
  );

  const setCombinedMode: Dispatch<SetStateAction<boolean>> = useCallback(
    (value) => {
      setCombinedModeState((current) => {
        const nextCombinedMode = typeof value === 'function' ? value(current) : value;
        persistPreference(contentTypeRef.current, nextCombinedMode);
        return nextCombinedMode;
      });
    },
    [persistPreference],
  );

  return {
    contentType,
    setContentType,
    combinedMode,
    setCombinedMode,
  };
};
