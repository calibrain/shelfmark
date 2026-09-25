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
  // Both values live in one state object so each setter can derive the other
  // from a pure updater instead of mirroring it into a ref during render.
  const [preference, setPreference] = useState<ContentTypePreference>(() =>
    resolveContentTypePreference(readStoredPreference(), null),
  );
  const { contentType, combinedMode, source } = preference;

  const setContentType: Dispatch<SetStateAction<ContentType>> = useCallback((value) => {
    setPreference((current) => ({
      ...current,
      contentType: typeof value === 'function' ? value(current.contentType) : value,
      source: 'chosen',
    }));
  }, []);

  const setCombinedMode: Dispatch<SetStateAction<boolean>> = useCallback((value) => {
    setPreference((current) => ({
      ...current,
      combinedMode: typeof value === 'function' ? value(current.combinedMode) : value,
      source: 'chosen',
    }));
  }, []);

  // The config arrives after the first render, so the configured default is applied here.
  // It only lands while nothing has chosen a tab, which leaves a deep-linked content type
  // and a stored choice both untouched, and it does not count as a choice itself.
  useDependencyEffect(() => {
    if (!serverDefault) {
      return;
    }
    setPreference((current) =>
      current.source === 'unset' ? resolveContentTypePreference(null, serverDefault) : current,
    );
  }, [serverDefault]);

  // Persist only what the user actually chose. Writing on every load would store a default
  // nobody picked, which is what stopped the configured default from ever being seen.
  useDependencyEffect(() => {
    if (source !== 'chosen') {
      return;
    }
    try {
      localStorage.setItem(CONTENT_TYPE_STORAGE_KEY, combinedMode ? 'combined' : contentType);
    } catch {
      // localStorage may be unavailable in private browsing
    }
  }, [contentType, combinedMode, source]);

  return {
    contentType,
    setContentType,
    combinedMode,
    setCombinedMode,
  };
};
