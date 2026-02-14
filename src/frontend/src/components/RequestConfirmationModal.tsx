import { useCallback, useEffect, useMemo, useState } from 'react';
import { CreateRequestPayload } from '../types';
import {
  applyRequestNoteToPayload,
  buildRequestConfirmationPreview,
  MAX_REQUEST_NOTE_LENGTH,
  truncateRequestNote,
} from '../utils/requestConfirmation';

interface RequestConfirmationModalProps {
  payload: CreateRequestPayload | null;
  allowNotes: boolean;
  onConfirm: (payload: CreateRequestPayload) => Promise<boolean>;
  onClose: () => void;
}

export const RequestConfirmationModal = ({
  payload,
  allowNotes,
  onConfirm,
  onClose,
}: RequestConfirmationModalProps) => {
  const [note, setNote] = useState('');
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
    if (payload) {
      setNote('');
      setIsSubmitting(false);
      setIsClosing(false);
    }
  }, [payload]);

  useEffect(() => {
    if (!payload) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [payload]);

  useEffect(() => {
    if (!payload) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleClose();
      }
    };
    document.addEventListener('keydown', onEscape);
    return () => document.removeEventListener('keydown', onEscape);
  }, [payload, handleClose]);

  const preview = useMemo(() => {
    return payload ? buildRequestConfirmationPreview(payload) : null;
  }, [payload]);

  if (!payload && !isClosing) return null;
  if (!payload) return null;
  if (!preview) return null;

  const titleId = 'request-confirmation-modal-title';
  const confirmDisabled = isSubmitting || (allowNotes && note.length > MAX_REQUEST_NOTE_LENGTH);

  const submit = async () => {
    if (confirmDisabled) {
      return;
    }

    setIsSubmitting(true);
    try {
      const nextPayload = applyRequestNoteToPayload(payload, note, allowNotes);
      const success = await onConfirm(nextPayload);
      if (!success) {
        setIsSubmitting(false);
      }
    } catch {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      className="modal-overlay active sm:px-6 sm:py-6"
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          handleClose();
        }
      }}
    >
      <div
        className={`details-container w-full max-w-md h-full sm:h-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-full sm:h-auto flex-col overflow-hidden rounded-none sm:rounded-2xl border-0 sm:border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-none sm:shadow-2xl">
          <header className="flex items-start gap-3 border-b border-[var(--border-muted)] px-5 py-4">
            <div className="flex-1">
              <h3 id={titleId} className="text-lg font-semibold">
                Request Book
              </h3>
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Close request confirmation"
              disabled={isSubmitting}
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </header>

          <div className="space-y-4 px-5 py-4">
            <div className="rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] px-3 py-3">
              <div className="flex gap-3">
                <div className="w-14 h-20 flex-shrink-0 rounded overflow-hidden border border-[var(--border-muted)] bg-[var(--bg-soft)]">
                  {preview.preview ? (
                    <img
                      src={preview.preview}
                      alt={`${preview.title} cover`}
                      className="w-full h-full object-cover object-top"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-[10px] opacity-60">
                      No cover
                    </div>
                  )}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold truncate">{preview.title}</p>
                  <p className="text-sm opacity-80 truncate">{preview.author}</p>
                  {preview.releaseLine && (
                    <p className="text-xs opacity-70 mt-1">{preview.releaseLine}</p>
                  )}
                </div>
              </div>
            </div>

            {allowNotes && (
              <div className="space-y-1">
                <label htmlFor="request-note" className="text-sm font-medium">
                  Note (optional)
                </label>
                <textarea
                  id="request-note"
                  value={note}
                  onChange={(event) => setNote(truncateRequestNote(event.target.value))}
                  maxLength={MAX_REQUEST_NOTE_LENGTH}
                  rows={4}
                  className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] text-sm resize-y min-h-[96px] focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500"
                  placeholder="Add context for admins reviewing this request..."
                  disabled={isSubmitting}
                />
                <p className="text-xs opacity-60 text-right">
                  {note.length}/{MAX_REQUEST_NOTE_LENGTH}
                </p>
              </div>
            )}
          </div>

          <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-muted)] px-5 py-4">
            <button
              type="button"
              onClick={handleClose}
              disabled={isSubmitting}
              className="px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)] bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={confirmDisabled}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed inline-flex items-center gap-2"
            >
              {isSubmitting ? (
                <>
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Requesting...
                </>
              ) : (
                'Request'
              )}
            </button>
          </footer>
        </div>
      </div>
    </div>
  );
};
