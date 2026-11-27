import type { Book, ContentType } from '../../types';
import type { PendingRequestKeys } from '../../utils/requestedBooks';
import { requestedFormats } from '../../utils/requestedBooks';
import { FormatIcon } from './LibraryBadges';

const LABELS: Record<ContentType, string> = {
  ebook: 'Ebook already requested, waiting on a decision',
  audiobook: 'Audiobook already requested, waiting on a decision',
};

const TEXT: Record<ContentType, string> = {
  ebook: 'Ebook requested',
  audiobook: 'Audiobook requested',
};

interface RequestedBadgesProps {
  book: Book;
  pendingRequests: PendingRequestKeys;
  /** Solid pills for use over cover art; otherwise tinted inline pills. */
  overlay?: boolean;
  className?: string;
}

/**
 * "Already requested" badges, one per format with a request still awaiting a
 * decision. Renders nothing when none.
 *
 * Amber rather than the library pills' sky, because the two say different
 * things: one is settled, the other is waiting on an admin. Over cover art the
 * pill is the format icon and "Requested", since "Audiobook requested" does
 * not fit a 120px cover; the tooltip carries the full sentence either way.
 */
export function RequestedBadges({
  book,
  pendingRequests,
  overlay = false,
  className = '',
}: RequestedBadgesProps) {
  const requested = requestedFormats(book, pendingRequests);
  if (requested.length === 0) return null;

  const pill = overlay
    ? 'rounded-md border border-amber-700 bg-amber-600 px-1.5 py-0.5 text-[10px] font-bold text-white'
    : 'rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:text-amber-300';
  const style = overlay
    ? { boxShadow: '0 2px 8px rgba(0, 0, 0, 0.4), 0 1px 3px rgba(0, 0, 0, 0.3)' }
    : undefined;

  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {requested.map((format) => (
        <span
          key={format}
          className={`flex items-center gap-0.5 ${pill}`}
          style={style}
          title={LABELS[format]}
          aria-label={LABELS[format]}
        >
          <FormatIcon format={format} className="h-3 w-3" />
          {overlay ? 'Requested' : TEXT[format]}
        </span>
      ))}
    </div>
  );
}
