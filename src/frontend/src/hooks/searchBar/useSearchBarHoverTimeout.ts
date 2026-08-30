import { useCallback, useRef } from 'react';

import { useMountEffect } from '@/hooks/useMountEffect';

export const useSearchBarHoverTimeout = () => {
  const hoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearHoverTimeout = useCallback(() => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
      hoverTimeoutRef.current = null;
    }
  }, []);

  // Scheduling stays inside the hook that owns the ref, so callers never mutate
  // a value handed back to them.
  const scheduleHoverTimeout = useCallback((callback: () => void, delay: number) => {
    hoverTimeoutRef.current = setTimeout(() => {
      hoverTimeoutRef.current = null;
      callback();
    }, delay);
  }, []);

  useMountEffect(() => {
    return () => {
      clearHoverTimeout();
    };
  });

  return {
    clearHoverTimeout,
    scheduleHoverTimeout,
  };
};
