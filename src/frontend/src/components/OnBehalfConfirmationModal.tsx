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
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      return;
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
      <div
        className={`absolute inset-0 bg-black/50 backdrop-blur-sm transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        onClick={handleClose}
      />

      <div
        className={`relative w-full max-w-lg rounded-xl border border-[var(--border-muted)] shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="flex items-center justify-between border-b border-[var(--border-muted)] px-6 py-4">
          <h3 id={titleId} className="text-lg font-semibold">
            Download as {actingAsName}?
          </h3>
          <button
            type="button"
            onClick={handleClose}
            className="p-1.5 rounded-lg hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Close download confirmation"
            disabled={isSubmitting}
          >
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="space-y-3 px-6 py-5">
          <p className="text-sm opacity-90">
            This download will use {actingAsName}&apos;s output preferences and destination settings.
          </p>
          <div className="rounded-xl border border-[var(--border-muted)] bg-[var(--bg-soft)] px-4 py-3">
            <p className="text-xs uppercase tracking-wide opacity-60">Title</p>
            <p className="text-sm font-medium mt-1 break-words">{itemTitle}</p>
          </div>
        </div>

        <footer className="flex items-center justify-end gap-3 border-t border-[var(--border-muted)] px-6 py-4">
          <button
            type="button"
            onClick={handleClose}
            disabled={isSubmitting}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-[var(--bg-soft)] border border-[var(--border-muted)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={confirmDisabled}
            className="px-5 py-2 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed inline-flex items-center gap-2"
          >
            {isSubmitting ? 'Queuing...' : 'Confirm'}
          </button>
        </footer>
      </div>
    </div>
  );
};
