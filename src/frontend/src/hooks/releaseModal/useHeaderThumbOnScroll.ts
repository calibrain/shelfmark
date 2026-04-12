import { useLayoutEffect, useState, type RefObject } from 'react';

interface UseHeaderThumbOnScrollOptions {
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  bookSummaryRef: RefObject<HTMLDivElement | null>;
}

export function useHeaderThumbOnScroll({
  scrollContainerRef,
  bookSummaryRef,
}: UseHeaderThumbOnScrollOptions): boolean {
  const [showHeaderThumb, setShowHeaderThumb] = useState(false);

  useLayoutEffect(() => {
    const scrollContainer = scrollContainerRef.current;
    const bookSummary = bookSummaryRef.current;
    if (!scrollContainer || !bookSummary) {
      return undefined;
    }

    const updateVisibility = () => {
      const summaryRect = bookSummary.getBoundingClientRect();
      const containerRect = scrollContainer.getBoundingClientRect();
      setShowHeaderThumb(summaryRect.bottom < containerRect.top + 20);
    };

    updateVisibility();

    scrollContainer.addEventListener('scroll', updateVisibility, { passive: true });
    window.addEventListener('resize', updateVisibility);

    return () => {
      scrollContainer.removeEventListener('scroll', updateVisibility);
      window.removeEventListener('resize', updateVisibility);
    };
  }, [bookSummaryRef, scrollContainerRef]);

  return showHeaderThumb;
}
