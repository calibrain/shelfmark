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

  useMountEffect(() => {
    return () => {
      clearHoverTimeout();
    };
  });

  return {
    hoverTimeoutRef,
    clearHoverTimeout,
  };
};
