import type { ReactNode } from 'react';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useDismiss } from '../hooks/useDismiss';

// Simple throttle function to limit how often a function can be called
function throttle<Args extends unknown[]>(
  fn: (...args: Args) => void,
  delay: number,
): (...args: Args) => void {
  let lastCall = 0;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;

  return (...args: Args) => {
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
  };
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
  const triggerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [panelDirection, setPanelDirection] = useState<'down' | 'up'>('down');
  const [panelPos, setPanelPos] = useState({ top: 0, left: 0, width: 0 });

  let triggerBorderRadius = '0.5rem';
  if (triggerChrome === 'minimal') {
    triggerBorderRadius = '0';
  } else if (isOpen) {
    triggerBorderRadius = panelDirection === 'down' ? '0.5rem 0.5rem 0 0' : '0 0 0.5rem 0.5rem';
  }

  let panelBorderRadius = '0.5rem';
  if (!renderTrigger) {
    panelBorderRadius = panelDirection === 'down' ? '0 0 0.5rem 0.5rem' : '0.5rem 0.5rem 0 0';
  }

  const toggleOpen = () => {
    if (disabled) return;
    setIsOpen((prev) => {
      const next = !prev;
      onOpenChange?.(next);
      return next;
    });
  };

  const close = useCallback(() => {
    setIsOpen(false);
    onOpenChange?.(false);
  }, [onOpenChange]);

  useDismiss(isOpen, [containerRef, panelRef], close);

  // Compute panel direction and fixed position relative to the trigger
  const updatePanelPosition = useCallback(() => {
    if (!triggerRef.current || !panelRef.current) return;

    const rect = triggerRef.current.getBoundingClientRect();
    const panelHeight = panelRef.current.offsetHeight || panelRef.current.scrollHeight;
    const panelWidth = panelRef.current.offsetWidth || panelRef.current.scrollWidth;

    // Direction: flip up if not enough space below but enough above
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const shouldOpenUp = spaceBelow < panelHeight && spaceAbove >= panelHeight;
    setPanelDirection(shouldOpenUp ? 'up' : 'down');

    // Vertical: seamless trigger uses -1px border overlap, custom trigger uses 8px gap
    let top: number;
    if (shouldOpenUp) {
      top = renderTrigger ? rect.top - panelHeight - 8 : rect.top - panelHeight + 1;
    } else {
      top = renderTrigger ? rect.bottom + 8 : rect.bottom - 1;
    }

    // Horizontal alignment
    let left: number;
    if (align === 'auto') {
      const overflowsRight = rect.left + panelWidth > window.innerWidth - 8;
      const overflowsLeft = rect.right - panelWidth < 8;
      left =
        overflowsRight && !overflowsLeft
          ? rect.right - Math.max(panelWidth, rect.width)
          : rect.left;
    } else if (align === 'right') {
      left = rect.right - Math.max(panelWidth, rect.width);
    } else {
      left = rect.left;
    }

    setPanelPos({ top, left, width: rect.width });
  }, [align, renderTrigger]);

  useLayoutEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    // Throttle scroll/resize handlers to reduce layout thrashing
    const throttledUpdate = throttle(updatePanelPosition, 100);

    updatePanelPosition();
    window.addEventListener('resize', throttledUpdate);
    window.addEventListener('scroll', throttledUpdate, true);

    return () => {
      window.removeEventListener('resize', throttledUpdate);
      window.removeEventListener('scroll', throttledUpdate, true);
    };
  }, [isOpen, updatePanelPosition]);

  return (
    <div className={widthClassName} ref={containerRef}>
      {label && (
        <label
          className="mb-1.5 block text-xs font-medium text-gray-500 dark:text-gray-400"
          onClick={toggleOpen}
        >
          {label}
        </label>
      )}
      <div ref={triggerRef}>
        {renderTrigger ? (
          renderTrigger({ isOpen, toggle: toggleOpen })
        ) : (
          <button
            type="button"
            onClick={toggleOpen}
            disabled={disabled}
            className={`flex w-full items-center justify-between gap-2 border px-3 py-2 text-left text-sm focus:outline-hidden focus-visible:ring-0 focus-visible:ring-offset-0 focus-visible:outline-hidden ${triggerChrome !== 'minimal' ? 'dropdown-trigger' : ''} ${buttonClassName}`}
            style={{
              color: 'var(--text)',
              borderColor: triggerChrome === 'minimal' ? 'transparent' : 'var(--border-muted)',
              borderWidth: triggerChrome === 'minimal' ? 0 : undefined,
              borderRadius: triggerBorderRadius,
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

        {isOpen &&
          createPortal(
            <div
              ref={panelRef}
              className={`border ${panelDirection === 'down' ? 'shadow-lg' : ''} ${panelClassName ?? ''}`}
              style={{
                position: 'fixed',
                top: panelPos.top,
                left: panelPos.left,
                width: panelClassName ? undefined : panelPos.width,
                zIndex: 100,
                background: 'var(--bg)',
                borderColor: 'var(--border-muted)',
                borderRadius: panelBorderRadius,
              }}
            >
              <div className={noScrollLimit ? '' : 'max-h-64 overflow-auto'}>
                {children({ close })}
              </div>
            </div>,
            document.body,
          )}
      </div>
    </div>
  );
};
