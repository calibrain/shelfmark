import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

// Find the closest scrollable ancestor element
function getScrollableAncestor(element: HTMLElement | null): HTMLElement | null {
  let current = element?.parentElement;
  while (current) {
    const style = getComputedStyle(current);
    const overflowY = style.overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'hidden') {
      return current;
    }
    current = current.parentElement;
  }
  return null;
}

// Simple throttle function to limit how often a function can be called
function throttle<T extends (...args: unknown[]) => void>(fn: T, delay: number): T {
  let lastCall = 0;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;

  return ((...args: unknown[]) => {
    const now = Date.now();
    const timeSinceLastCall = now - lastCall;

    if (timeSinceLastCall >= delay) {
      lastCall = now;
      fn(...args);
    } else if (!timeoutId) {
      // Schedule a trailing call
      timeoutId = setTimeout(() => {
        lastCall = Date.now();
        timeoutId = null;
        fn(...args);
      }, delay - timeSinceLastCall);
    }
  }) as T;
}

interface DropdownProps {
  label?: string;
  summary?: ReactNode;
  children: (helpers: { close: () => void }) => ReactNode;
  align?: 'left' | 'right' | 'auto';
  widthClassName?: string;
  buttonClassName?: string;
  panelClassName?: string;
  disabled?: boolean;
  renderTrigger?: (props: { isOpen: boolean; toggle: () => void }) => ReactNode;
  /** Disable max-height and overflow scrolling (for panels with nested dropdowns) */
  noScrollLimit?: boolean;
  triggerChrome?: 'default' | 'minimal';
  onOpenChange?: (isOpen: boolean) => void;
}

export const Dropdown = ({
  label,
  summary,
  children,
  align = 'left',
  widthClassName = 'w-full',
  buttonClassName = '',
  panelClassName = '',
  disabled = false,
  renderTrigger,
  noScrollLimit = false,
  triggerChrome = 'default',
  onOpenChange,
}: DropdownProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [panelDirection, setPanelDirection] = useState<'down' | 'up'>('down');
  const [resolvedAlign, setResolvedAlign] = useState<'left' | 'right'>(align === 'right' ? 'right' : 'left');

  const toggleOpen = () => {
    if (disabled) return;
    setIsOpen(prev => {
      const next = !prev;
      onOpenChange?.(next);
      return next;
    });
  };

  const close = () => {
    setIsOpen(false);
    onOpenChange?.(false);
  };

  useEffect(() => {
    if (!isOpen) return;

    const handleClick = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        close();
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        close();
      }
    };

    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleEscape);

    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);

  // Memoize the panel direction calculation
  const updatePanelDirection = useCallback(() => {
    if (!containerRef.current || !panelRef.current) {
      return;
    }

    const rect = containerRef.current.getBoundingClientRect();
    const panelHeight = panelRef.current.offsetHeight || panelRef.current.scrollHeight;

    // Check if we're inside a scrollable container and use its bounds
    const scrollableAncestor = getScrollableAncestor(containerRef.current);
    const containerBottom = scrollableAncestor
      ? scrollableAncestor.getBoundingClientRect().bottom
      : window.innerHeight;
    const containerTop = scrollableAncestor
      ? scrollableAncestor.getBoundingClientRect().top
      : 0;

    const spaceBelow = containerBottom - rect.bottom - 8;
    const spaceAbove = rect.top - containerTop - 8;
    const shouldOpenUp = spaceBelow < panelHeight && spaceAbove >= panelHeight;

    setPanelDirection(shouldOpenUp ? 'up' : 'down');

    // Auto horizontal alignment: check if panel overflows viewport right/left
    if (align === 'auto') {
      const panelWidth = panelRef.current.offsetWidth || panelRef.current.scrollWidth;
      const overflowsRight = rect.left + panelWidth > window.innerWidth - 8;
      const overflowsLeft = rect.right - panelWidth < 8;

      if (overflowsRight && !overflowsLeft) {
        setResolvedAlign('right');
      } else if (overflowsLeft && !overflowsRight) {
        setResolvedAlign('left');
      } else {
        setResolvedAlign('left');
      }
    } else {
      setResolvedAlign(align === 'right' ? 'right' : 'left');
    }
  }, [align]);

  useLayoutEffect(() => {
    if (!isOpen) return;

    // Throttle scroll/resize handlers to reduce layout thrashing
    const throttledUpdate = throttle(updatePanelDirection, 100);

    updatePanelDirection();
    window.addEventListener('resize', throttledUpdate);
    window.addEventListener('scroll', throttledUpdate, true);

    return () => {
      window.removeEventListener('resize', throttledUpdate);
      window.removeEventListener('scroll', throttledUpdate, true);
    };
  }, [isOpen, updatePanelDirection]);

  return (
    <div className={widthClassName} ref={containerRef}>
      {label && (
        <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5" onClick={toggleOpen}>
          {label}
        </label>
      )}
      <div className="relative">
        {renderTrigger ? (
          renderTrigger({ isOpen, toggle: toggleOpen })
        ) : (
          <button
            type="button"
            onClick={toggleOpen}
            disabled={disabled}
            className={`w-full px-3 py-2 text-sm border flex items-center justify-between gap-2 text-left focus:outline-hidden focus-visible:outline-hidden focus-visible:ring-0 focus-visible:ring-offset-0 ${triggerChrome !== 'minimal' ? 'dropdown-trigger' : ''} ${buttonClassName}`}
            style={{
              color: 'var(--text)',
              borderColor: triggerChrome === 'minimal' ? 'transparent' : 'var(--border-muted)',
              borderWidth: triggerChrome === 'minimal' ? 0 : undefined,
              borderRadius: isOpen
                ? triggerChrome === 'minimal'
                  ? '0'
                  : panelDirection === 'down'
                    ? '0.5rem 0.5rem 0 0'
                    : '0 0 0.5rem 0.5rem'
                : triggerChrome === 'minimal'
                  ? '0'
                  : '0.5rem',
            }}
          >
            <span className="min-w-0 flex-1 truncate">
              {summary ?? <span className="opacity-60">Select an option</span>}
            </span>
            <svg
              className={`h-4 w-4 shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
            </svg>
          </button>
        )}

        {isOpen && (
          <div
            ref={panelRef}
            className={`absolute ${resolvedAlign === 'right' ? 'right-0' : 'left-0'} ${
              panelDirection === 'down'
                ? renderTrigger ? 'mt-2' : ''
                : renderTrigger ? 'bottom-full mb-2' : 'bottom-full'
            } border z-20 ${panelDirection === 'down' ? 'shadow-lg' : ''} ${panelClassName || widthClassName}`}
            style={{
              background: 'var(--bg)',
              borderColor: 'var(--border-muted)',
              borderRadius: renderTrigger
                ? '0.5rem'
                : panelDirection === 'down'
                  ? '0 0 0.5rem 0.5rem'
                  : '0.5rem 0.5rem 0 0',
              marginTop: !renderTrigger && panelDirection === 'down' ? '-1px' : undefined,
              marginBottom: !renderTrigger && panelDirection === 'up' ? '-1px' : undefined,
            }}
          >
            <div className={noScrollLimit ? '' : 'max-h-64 overflow-auto'}>
              {children({ close })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
