import { useEffect, useEffectEvent, type RefObject } from 'react';

export const useDismiss = (
  isOpen: boolean,
  refs: RefObject<HTMLElement | null>[],
  onClose: () => void,
) => {
  const handlePointerDown = useEffectEvent((event: MouseEvent) => {
    const target = event.target;
    if (!(target instanceof Node)) {
      return;
    }

    if (refs.some((ref) => ref.current?.contains(target))) {
      return;
    }

    onClose();
  });

  const handleEscape = useEffectEvent((event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      onClose();
    }
  });

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const handleClickOutside = (event: MouseEvent) => handlePointerDown(event);
    const handleKeyDown = (event: KeyboardEvent) => handleEscape(event);

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);
};
