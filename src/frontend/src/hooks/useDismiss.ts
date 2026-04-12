import { useEffect, useRef, type RefObject } from 'react';

export const useDismiss = (
  isOpen: boolean,
  refs: RefObject<HTMLElement | null>[],
  onClose: () => void,
) => {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

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

      onCloseRef.current();
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onCloseRef.current();
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
