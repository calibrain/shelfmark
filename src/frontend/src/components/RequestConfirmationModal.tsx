import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { getMetadataBookInfo } from '../services/api';
import { CreateRequestPayload } from '../types';
import {
  applyRequestNoteToPayload,
  buildRequestConfirmationPreview,
  enrichPreviewFromBook,
  MAX_REQUEST_NOTE_LENGTH,
  RequestConfirmationPreview,
  truncateRequestNote,
} from '../utils/requestConfirmation';
import { isSourceBackedRequestPayload } from '../utils/requestPayload';

interface RequestConfirmationModalProps {
  payload: CreateRequestPayload | null;
  extraPayloads?: CreateRequestPayload[];
  allowNotes: boolean;
  onConfirm: (
    payload: CreateRequestPayload,
    extraPayloads?: CreateRequestPayload[],
  ) => Promise<boolean>;
  onClose: () => void;
}

export const RequestConfirmationModal = ({
  payload,
  extraPayloads = [],
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
    if (!payload) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [payload]);

  useEffect(() => {
    if (!payload) {
      return undefined;
    }
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleClose();
      }
    };
    document.addEventListener('keydown', onEscape);
    return () => document.removeEventListener('keydown', onEscape);
  }, [payload, handleClose]);

  const basePreview = useMemo(() => {
    return payload ? buildRequestConfirmationPreview(payload) : null;
  }, [payload]);

  const extraPreviews = useMemo(() => {
    return extraPayloads.map(buildRequestConfirmationPreview);
  }, [extraPayloads]);

  const [enriched, setEnriched] = useState<RequestConfirmationPreview | null>(null);
  const enrichRef = useRef(0);

  useEffect(() => {
    setEnriched(null);
    if (!payload) return;

    const bookData = payload.book_data || {};
    const provider = bookData.provider;
    const providerId = bookData.provider_id;

    // Only fetch for metadata providers, and skip if series info is already present
    if (
      typeof provider !== 'string' ||
      !provider ||
      typeof providerId !== 'string' ||
      !providerId ||
      isSourceBackedRequestPayload(payload) ||
      bookData.series_name
    ) {
      return;
    }

    const id = ++enrichRef.current;
    getMetadataBookInfo(provider, providerId)
      .then((book) => {
        if (id !== enrichRef.current) return;
        if (book.series_name) {
          setEnriched((prev) => enrichPreviewFromBook(prev ?? basePreview!, book));
        }
      })
      .catch(() => {
        // Enrichment is best-effort; ignore failures
      });
  }, [payload, basePreview]);

  const preview = enriched ?? basePreview;

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
      const success = await onConfirm(
        nextPayload,
        extraPayloads.length > 0 ? extraPayloads : undefined,
      );
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
        className={`absolute inset-0 bg-black/50 backdrop-blur-xs transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        onClick={handleClose}
      />

      <div
        className={`relative w-full max-w-xl rounded-xl border border-(--border-muted) shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="flex items-center justify-between border-b border-(--border-muted) px-6 py-4">
          <h3 id={titleId} className="text-lg font-semibold">
            {extraPayloads.length > 0 ? 'Request Book & Audiobook' : 'Request Book'}
          </h3>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-lg p-1.5 transition-colors hover:bg-(--hover-surface) disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Close request confirmation"
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

        <div className="space-y-4 px-6 py-5">
          <div className="rounded-xl border border-(--border-muted) bg-(--bg-soft) px-4 py-4">
            <div className="flex gap-4">
              <div className="h-24 w-16 shrink-0 overflow-hidden rounded-lg border border-(--border-muted) bg-(--bg)">
                {preview.preview ? (
                  <img
                    src={preview.preview}
                    alt={`${preview.title} cover`}
                    className="h-full w-full object-cover object-top"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center text-[10px] opacity-60">
                    No cover
                  </div>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm leading-snug font-semibold">{preview.title}</p>
                <p className="mt-1 text-sm opacity-80">{preview.author}</p>
                {(preview.year || preview.seriesLine) && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    {preview.year && <span className="text-xs opacity-60">{preview.year}</span>}
                    {preview.year && preview.seriesLine && (
                      <span className="text-xs opacity-40">·</span>
                    )}
                    {preview.seriesLine && (
                      <span className="text-xs opacity-60">{preview.seriesLine}</span>
                    )}
                  </div>
                )}
                {/* Release lines — show all (primary + extras) with content type labels when combined */}
                {(preview.releaseLine || extraPreviews.length > 0) && (
                  <div className="mt-1.5 space-y-0.5">
                    {preview.releaseLine && (
                      <p className="text-xs opacity-60">
                        {extraPreviews.length > 0 && (
                          <span className="font-medium opacity-80">
                            {payload.context.content_type === 'ebook' ? 'Book: ' : 'Audiobook: '}
                          </span>
                        )}
                        {preview.releaseLine}
                      </p>
                    )}
                    {extraPreviews.map(
                      (ep, i) =>
                        ep.releaseLine && (
                          <p key={i} className="text-xs opacity-60">
                            <span className="font-medium opacity-80">
                              {extraPayloads[i]?.context.content_type === 'ebook'
                                ? 'Book: '
                                : 'Audiobook: '}
                            </span>
                            {ep.releaseLine}
                          </p>
                        ),
                    )}
                  </div>
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
                className="min-h-[96px] w-full resize-y rounded-lg border border-(--border-muted) bg-(--bg) px-3 py-2 text-sm focus:border-sky-500 focus:ring-2 focus:ring-sky-500/50 focus:outline-hidden"
                placeholder="Add context for admins reviewing this request..."
                disabled={isSubmitting}
              />
              <p className="text-right text-xs opacity-60">
                {note.length}/{MAX_REQUEST_NOTE_LENGTH}
              </p>
            </div>
          )}
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
            {isSubmitting ? (
              <>
                <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                    fill="none"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
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
  );
};
