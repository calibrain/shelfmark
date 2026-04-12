import { useEffect, useRef, useState } from 'react';

import type { Toast } from '../types';

interface ToastContainerProps {
  toasts: Toast[];
}

export const ToastContainer = ({ toasts }: ToastContainerProps) => {
  const [visibleToasts, setVisibleToasts] = useState(new Set<string>());
  const visibleToastsRef = useRef(visibleToasts);

  useEffect(() => {
    visibleToastsRef.current = visibleToasts;
  }, [visibleToasts]);

  useEffect(() => {
    const timeoutIds: number[] = [];

    toasts.forEach((toast) => {
      if (!visibleToastsRef.current.has(toast.id)) {
        const timeoutId = window.setTimeout(() => {
          setVisibleToasts((prev) => new Set([...prev, toast.id]));
        }, 10);
        timeoutIds.push(timeoutId);
      }
    });

    return () => {
      timeoutIds.forEach((timeoutId) => window.clearTimeout(timeoutId));
    };
  }, [toasts]);

  const toastTypeClasses: Record<Toast['type'], string> = {
    success: 'bg-green-600 text-white',
    error: 'bg-red-600 text-white',
    info: 'bg-blue-600 text-white',
  };

  return (
    <div id="toast-container" className="fixed right-4 bottom-4 z-1100 space-y-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`toast-notification rounded-md px-4 py-3 text-sm font-medium shadow-lg transition-all duration-300 ${
            toastTypeClasses[toast.type]
          } ${visibleToasts.has(toast.id) ? 'toast-visible' : ''}`}
        >
          {toast.message}
        </div>
      ))}
    </div>
  );
};
