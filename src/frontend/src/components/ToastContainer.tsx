import type { Toast } from '../types';

interface ToastContainerProps {
  toasts: Toast[];
}

export const ToastContainer = ({ toasts }: ToastContainerProps) => {
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
          className={`toast-notification toast-visible animate-pop-up rounded-md px-4 py-3 text-sm font-medium shadow-lg transition-all duration-300 ${toastTypeClasses[toast.type]}`}
        >
          {toast.message}
        </div>
      ))}
    </div>
  );
};
