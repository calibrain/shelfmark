import { useCallback, useEffect, useState } from 'react';
import { EmailRecipient } from '../types';

interface EmailRecipientModalProps {
  isOpen: boolean;
  recipients: EmailRecipient[];
  onSelect: (nickname: string) => void;
  onCancel: () => void;
}

export const EmailRecipientModal = ({ isOpen, recipients, onSelect, onCancel }: EmailRecipientModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [selectedNickname, setSelectedNickname] = useState<string | null>(null);

  // Reset selection when modal opens/closes
  useEffect(() => {
    if (isOpen) setSelectedNickname(null);
  }, [isOpen]);

  const handleCancel = useCallback(() => {
    if (isClosing) return;
    setIsClosing(true);
    setTimeout(() => {
      onCancel();
      setIsClosing(false);
    }, 150);
  }, [onCancel, isClosing]);

  const handleSend = useCallback(() => {
    if (isClosing || !selectedNickname) return;
    setIsClosing(true);
    setTimeout(() => {
      onSelect(selectedNickname);
      setIsClosing(false);
    }, 150);
  }, [onSelect, selectedNickname, isClosing]);

  // ESC to cancel
  useEffect(() => {
    if (!isOpen) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleCancel();
      }
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, handleCancel]);

  // Prevent body scroll while open
  useEffect(() => {
    if (!isOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const titleId = 'email-recipient-modal-title';

  return (
    <div
      className="modal-overlay active sm:px-6 sm:py-6"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleCancel();
      }}
    >
      <div
        className={`w-full sm:max-w-md h-full sm:h-auto pointer-events-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-full sm:h-auto flex-col overflow-hidden rounded-none sm:rounded-2xl border-0 sm:border border-[var(--border-muted)] bg-[var(--bg)] text-[var(--text)] shadow-none sm:shadow-2xl">
          <header className="flex items-start gap-4 border-b border-[var(--border-muted)] bg-[var(--bg)] px-5 py-4">
            <div className="flex-1 space-y-1">
              <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Output</p>
              <h3 id={titleId} className="text-lg font-semibold leading-snug">
                Send via Email
              </h3>
              <p className="text-sm text-gray-600 dark:text-gray-300">
                Choose a recipient for this download.
              </p>
            </div>
            <button
              type="button"
              onClick={handleCancel}
              className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
              aria-label="Cancel"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </header>

          <div className="flex-1 min-h-0 overflow-y-auto px-5 py-6">
            {recipients.length === 0 ? (
              <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg-soft)] px-4 py-3 text-sm">
                No recipients configured.
              </div>
            ) : (
              <div className="space-y-2">
                {recipients.map((r) => {
                  const isSelected = selectedNickname === r.nickname;
                  return (
                    <button
                      key={`${r.nickname}:${r.email}`}
                      type="button"
                      onClick={() => setSelectedNickname(isSelected ? null : r.nickname)}
                      className={`w-full text-left rounded-2xl border px-4 py-3 transition-colors hover-action ${
                        isSelected
                          ? 'border-sky-500 bg-sky-500/10'
                          : 'border-[var(--border-muted)] bg-[var(--bg-soft)]'
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <div
                          className={`flex-shrink-0 w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors ${
                            isSelected ? 'border-sky-500 bg-sky-500' : 'border-gray-400 dark:border-gray-500'
                          }`}
                        >
                          {isSelected && (
                            <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={3}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                          )}
                        </div>
                        <div className="min-w-0">
                          <div className="font-medium truncate">{r.nickname}</div>
                          <div className="text-xs opacity-70 truncate">{r.email}</div>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Desktop footer: small Send + Cancel buttons */}
          <footer className="hidden sm:flex border-t border-[var(--border-muted)] bg-[var(--bg)] px-5 py-4 justify-end gap-2">
            <button
              type="button"
              onClick={handleCancel}
              className="px-4 py-2 rounded-lg text-sm font-medium
                         bg-[var(--bg-soft)] border border-[var(--border-muted)]
                         hover:bg-[var(--hover-surface)] transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSend}
              disabled={!selectedNickname}
              className="px-4 py-2 rounded-lg text-sm font-medium transition-colors
                         bg-sky-600 text-white hover:bg-sky-700
                         disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Send
            </button>
          </footer>

          {/* Mobile: full-width Send button, slides up when a recipient is selected */}
          {selectedNickname && (
            <div
              className="sm:hidden flex-shrink-0 px-6 py-4 border-t border-[var(--border-muted)] bg-[var(--bg)] animate-slide-up"
              style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))' }}
            >
              <button
                type="button"
                onClick={handleSend}
                className="w-full py-2.5 px-4 rounded-lg font-medium transition-colors
                           bg-sky-600 text-white hover:bg-sky-700"
              >
                Send
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
