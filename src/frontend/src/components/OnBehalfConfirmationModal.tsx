import { useCallback, useEffect, useState } from 'react';

interface OnBehalfConfirmationModalProps {
  isOpen: boolean;
  actingAsName: string;
  itemTitle: string;
  onConfirm: () => Promise<boolean>;
  onClose: () => void;
}

export const OnBehalfConfirmationModal = ({
  isOpen,
  actingAsName,
  itemTitle,
  onConfirm,
  onClose,
}: OnBehalfConfirmationModalProps) => {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isClosing, setIsClosing] = useState(false);

  const handleClose = useCallback(() => {
    if (isSubmitting) {
      return;
    }
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [isSubmitting, onClose]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setIsSubmitting(false);
    setIsClosing(false);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleClose();
      }
    };
    document.addEventListener('keydown', onEscape);
    return () => {
      document.removeEventListener('keydown', onEscape);
    };
  }, [isOpen, handleClose]);

  if (!isOpen && !isClosing) return null;
  if (!isOpen) return null;

  const titleId = 'on-behalf-confirmation-modal-title';
  const confirmDisabled = isSubmitting;

  const submit = async () => {
    if (confirmDisabled) {
      return;
    }

    setIsSubmitting(true);
    try {
      const success = await onConfirm();
      if (!success) {
        setIsSubmitting(false);
      }
    } catch {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className={`absolute inset-0 bg-black/50 backdrop-blur-xs transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        onClick={handleClose}
        tabIndex={-1}
        aria-label="Close download confirmation"
      />

      <div
        className={`relative w-full max-w-lg rounded-xl border border-(--border-muted) shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="flex items-center justify-between border-b border-(--border-muted) px-6 py-4">
          <h3 id={titleId} className="text-lg font-semibold">
            Download as {actingAsName}?
          </h3>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-lg p-1.5 transition-colors hover:bg-(--hover-surface) disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Close download confirmation"
            disabled={isSubmitting}
          >
            <svg
              className="h-5 w-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="space-y-3 px-6 py-5">
          <p className="text-sm opacity-90">
            This download will use {actingAsName}&apos;s output preferences and destination
            settings.
          </p>
          <div className="rounded-xl border border-(--border-muted) bg-(--bg-soft) px-4 py-3">
            <p className="text-xs tracking-wide uppercase opacity-60">Title</p>
            <p className="mt-1 text-sm font-medium wrap-break-word">{itemTitle}</p>
          </div>
        </div>

        <footer className="flex items-center justify-end gap-3 border-t border-(--border-muted) px-6 py-4">
          <button
            type="button"
            onClick={handleClose}
            disabled={isSubmitting}
            className="rounded-lg border border-(--border-muted) bg-(--bg-soft) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface) disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={confirmDisabled}
            className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting ? 'Queuing...' : 'Confirm'}
          </button>
        </footer>
      </div>
    </div>
  );
};
