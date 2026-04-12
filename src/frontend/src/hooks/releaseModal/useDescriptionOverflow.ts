import { useLayoutEffect, useState, type RefObject } from 'react';

interface UseDescriptionOverflowOptions {
  descriptionRef: RefObject<HTMLParagraphElement | null>;
  descriptionExpanded: boolean;
  descriptionKey?: string;
}

export function useDescriptionOverflow({
  descriptionRef,
  descriptionExpanded,
  descriptionKey,
}: UseDescriptionOverflowOptions): boolean {
  const [descriptionOverflows, setDescriptionOverflows] = useState(false);

  useLayoutEffect(() => {
    const el = descriptionRef.current;
    if (!el) {
      setDescriptionOverflows(false);
      return undefined;
    }

    const updateOverflow = () => {
      if (descriptionExpanded) {
        setDescriptionOverflows(false);
        return;
      }

      setDescriptionOverflows(el.scrollHeight > el.clientHeight);
    };

    updateOverflow();

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateOverflow);
      return () => {
        window.removeEventListener('resize', updateOverflow);
      };
    }

    const observer = new ResizeObserver(updateOverflow);
    observer.observe(el);

    return () => {
      observer.disconnect();
    };
  }, [descriptionExpanded, descriptionKey, descriptionRef]);

  return descriptionOverflows;
}
