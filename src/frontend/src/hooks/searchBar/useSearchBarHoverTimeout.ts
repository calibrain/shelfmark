import { useCallback, useEffect, useRef } from 'react';

export const useSearchBarHoverTimeout = () => {
  const hoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearHoverTimeout = useCallback(() => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
      hoverTimeoutRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      clearHoverTimeout();
    };
  }, [clearHoverTimeout]);

  return {
    hoverTimeoutRef,
    clearHoverTimeout,
  };
};
