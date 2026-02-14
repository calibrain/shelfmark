import { useEffect, useState } from 'react';

interface RejectDialogProps {
  requestId: number;
  bookTitle: string;
  onConfirm: (requestId: number, adminNote?: string) => Promise<void> | void;
  onCancel: () => void;
}

const MAX_ADMIN_NOTE_LENGTH = 1000;

export const RejectDialog = ({
  requestId,
  bookTitle,
  onConfirm,
  onCancel,
}: RejectDialogProps) => {
  const [adminNote, setAdminNote] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isSubmitting) {
        onCancel();
      }
    };

    document.addEventListener('keydown', onEscape);
    return () => document.removeEventListener('keydown', onEscape);
  }, [isSubmitting, onCancel]);

  const handleConfirm = async () => {
    if (isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    try {
      const trimmed = adminNote.trim();
      await onConfirm(requestId, trimmed || undefined);
      onCancel();
    } catch {
      // Parent handler surfaces the error state/toast.
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] p-3 mt-2 space-y-2">
      <p className="text-xs font-medium">
        Reject request for <span className="opacity-80">{bookTitle}</span>
      </p>
      <textarea
        value={adminNote}
        onChange={(event) => setAdminNote(event.target.value.slice(0, MAX_ADMIN_NOTE_LENGTH))}
        rows={3}
        maxLength={MAX_ADMIN_NOTE_LENGTH}
        placeholder="Optional note shown to the user"
        className="w-full px-2.5 py-2 rounded-md border border-[var(--border-muted)] bg-[var(--bg-soft)] text-xs resize-y min-h-[72px] focus:outline-none focus:ring-2 focus:ring-red-500/30 focus:border-red-500"
        disabled={isSubmitting}
      />
      <div className="flex items-center justify-between">
        <span className="text-[11px] opacity-60">{adminNote.length}/{MAX_ADMIN_NOTE_LENGTH}</span>
        <div className="inline-flex items-center gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="px-2.5 py-1.5 rounded-md text-xs border border-[var(--border-muted)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={isSubmitting}
            className="px-2.5 py-1.5 rounded-md text-xs font-medium text-white bg-red-600 hover:bg-red-700 transition-colors disabled:opacity-60"
          >
            {isSubmitting ? 'Rejecting...' : 'Reject'}
          </button>
        </div>
      </div>
    </div>
  );
};
