import { useEffect, useEffectEvent, useRef, type RefObject } from 'react';

export const useDismiss = (
  isOpen: boolean,
  refs: RefObject<HTMLElement | null>[],
  onClose: () => void,
) => {
  const handleClose = useEffectEvent(() => {
    onClose();
  });

  const refsRef = useRef(refs);
  refsRef.current = refs;

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }

      if (refsRef.current.some((ref) => ref.current?.contains(target))) {
        return;
      }

      handleClose();
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);
};
