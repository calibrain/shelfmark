import { useEffect, useEffectEvent } from 'react';

export function useEscapeKey(enabled: boolean, onEscape: () => void): void {
  const handleEscape = useEffectEvent(() => {
    onEscape();
  });

  useEffect(() => {
    if (!enabled) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleEscape();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [enabled]);
}
