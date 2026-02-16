import { useState, useRef, useEffect, ReactNode } from 'react';
import { createPortal } from 'react-dom';

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  position?: 'top' | 'bottom' | 'left' | 'right';
  delay?: number;
  className?: string;
  unstyled?: boolean;
  triggerClassName?: string;
  alwaysWrap?: boolean;
}

export function Tooltip({
  content,
  children,
  position = 'top',
  delay = 200,
  className = '',
  unstyled = false,
  triggerClassName = 'inline-flex max-w-full',
  alwaysWrap = false,
}: TooltipProps) {
  const [isVisible, setIsVisible] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);
  const hasContent = Boolean(content);
  const isPlainTextContent = typeof content === 'string' || typeof content === 'number';
  const spacing = 6;

  const showTooltip = () => {
    if (!hasContent) {
      return;
    }
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => {
      if (triggerRef.current) {
        const rect = triggerRef.current.getBoundingClientRect();
        let top = 0;
        let left = 0;

        switch (position) {
          case 'top':
            top = rect.top - spacing;
            left = rect.left + rect.width / 2;
            break;
          case 'bottom':
            top = rect.bottom + spacing;
            left = rect.left + rect.width / 2;
            break;
          case 'left':
            top = rect.top + rect.height / 2;
            left = rect.left - spacing;
            break;
          case 'right':
            top = rect.top + rect.height / 2;
            left = rect.right + spacing;
            break;
        }

        setCoords({ top, left });
        setIsVisible(true);
      }
    }, delay);
  };

  const hideTooltip = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    setIsVisible(false);
    setCoords(null);
  };

  useEffect(() => {
    if (!isVisible || !coords || !tooltipRef.current) {
      return;
    }

    const tooltipRect = tooltipRef.current.getBoundingClientRect();
    const viewportPadding = 6;

    let deltaX = 0;
    let deltaY = 0;

    if (tooltipRect.width + viewportPadding * 2 > window.innerWidth) {
      deltaX = viewportPadding - tooltipRect.left;
    } else {
      if (tooltipRect.left < viewportPadding) {
        deltaX = viewportPadding - tooltipRect.left;
      } else if (tooltipRect.right > window.innerWidth - viewportPadding) {
        deltaX = (window.innerWidth - viewportPadding) - tooltipRect.right;
      }
    }

    if (tooltipRect.height + viewportPadding * 2 > window.innerHeight) {
      deltaY = viewportPadding - tooltipRect.top;
    } else {
      if (tooltipRect.top < viewportPadding) {
        deltaY = viewportPadding - tooltipRect.top;
      } else if (tooltipRect.bottom > window.innerHeight - viewportPadding) {
        deltaY = (window.innerHeight - viewportPadding) - tooltipRect.bottom;
      }
    }

    if (deltaX !== 0 || deltaY !== 0) {
      setCoords((current) => {
        if (!current) {
          return current;
        }
        return {
          top: current.top + deltaY,
          left: current.left + deltaX,
        };
      });
    }
  }, [coords, isVisible]);

  useEffect(() => {
    if (!hasContent) {
      setIsVisible(false);
      setCoords(null);
    }
  }, [hasContent]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  if (!hasContent && !alwaysWrap) {
    return <>{children}</>;
  }

  // Transform classes to center tooltip relative to the anchor point
  const transformClass = {
    top: '-translate-x-1/2 -translate-y-full',
    bottom: '-translate-x-1/2',
    left: '-translate-x-full -translate-y-1/2',
    right: '-translate-y-1/2',
  }[position];
  const tooltipSizeClass = isPlainTextContent
    ? 'px-2 py-1 text-[11px] leading-tight rounded-md font-medium'
    : 'px-2.5 py-2 text-xs rounded-lg';

  return (
    <>
      <div
        ref={triggerRef}
        onMouseEnter={showTooltip}
        onMouseLeave={hideTooltip}
        onFocusCapture={showTooltip}
        onBlurCapture={hideTooltip}
        className={triggerClassName}
      >
        {children}
      </div>
      {hasContent && isVisible && coords && createPortal(
        <div
          ref={tooltipRef}
          role="tooltip"
          className={`fixed z-[9999] pointer-events-none ${tooltipSizeClass} ${transformClass} ${className}`}
          style={{
            top: coords.top,
            left: coords.left,
            ...(unstyled ? {} : {
              background: 'var(--bg)',
              color: 'var(--text)',
              border: isPlainTextContent ? 'none' : '1px solid var(--border-muted)',
              boxShadow: isPlainTextContent
                ? '0 8px 18px rgba(0, 0, 0, 0.28)'
                : '0 10px 22px rgba(0, 0, 0, 0.28)',
            }),
          }}
        >
          {content}
        </div>,
        document.body
      )}
    </>
  );
}

export default Tooltip;
