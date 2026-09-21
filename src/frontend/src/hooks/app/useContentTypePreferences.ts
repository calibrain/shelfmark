import { useCallback, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { ContentType } from '../../types';
import {
  CONTENT_TYPE_STORAGE_KEY,
  resolveContentTypePreference,
  type ContentTypePreference,
} from '../../utils/contentTypePreference';
import { useDependencyEffect } from '../useMountEffect';

const readStoredPreference = (): string | null => {
  try {
    return localStorage.getItem(CONTENT_TYPE_STORAGE_KEY);
  } catch {
    // localStorage may be unavailable in private browsing
    return null;
  }
};

export const useContentTypePreferences = (
  serverDefault?: ContentType | null,
): {
  contentType: ContentType;
  setContentType: Dispatch<SetStateAction<ContentType>>;
  combinedMode: boolean;
  setCombinedMode: Dispatch<SetStateAction<boolean>>;
} => {
  // Whether this browser had already picked a tab, captured before the effect below
  // writes one, so a server default is not mistaken for the user's own choice.
  const [hadStoredPreference] = useState(() => readStoredPreference() !== null);
  // Both values live in one state object so each setter can derive the other
  // from a pure updater instead of mirroring it into a ref during render.
  const [preference, setPreference] = useState<ContentTypePreference>(() =>
    resolveContentTypePreference(readStoredPreference(), serverDefault),
  );
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

  // The config arrives after the first render, so adopt the server default then,
  // but only for a browser that had nothing stored when the page loaded.
  useDependencyEffect(() => {
    if (hadStoredPreference || !serverDefault) {
      return;
    }
    setPreference(resolveContentTypePreference(null, serverDefault));
  }, [serverDefault, hadStoredPreference]);

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
