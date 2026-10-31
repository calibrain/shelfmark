import type { LibraryOwnership } from '../../types';

export type OwnedFormat = 'ebook' | 'audiobook';

const LABELS: Record<OwnedFormat, string> = {
  ebook: 'Ebook already in your library',
  audiobook: 'Audiobook already in your library',
};

const COLLECTION_LABELS: Record<OwnedFormat, string> = {
  ebook: 'Ebook in your library, inside a collection',
  audiobook: 'Audiobook in your library, inside a collection',
};

const SHORT: Record<OwnedFormat, string> = { ebook: 'Ebook', audiobook: 'Audiobook' };

/** Formats the library check reports as owned, in display order. */
export function ownedFormats(library?: LibraryOwnership | null): OwnedFormat[] {
  if (!library) return [];
  return (['ebook', 'audiobook'] as const).filter(
    (format) => library[format] === 'owned' || library[format] === 'collection',
  );
}

/** True when this format is only held inside a larger volume, not on its own. */
export function isCollectionHolding(
  library: LibraryOwnership | null | undefined,
  format: OwnedFormat,
): boolean {
  return library?.[format] === 'collection';
}

function FormatIcon({ format, className }: { format: OwnedFormat; className: string }) {
  if (format === 'ebook') {
    return (
      <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"
        />
      </svg>
    );
  }
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M4 14v-2a8 8 0 1116 0v2M4 14h3v6H4zm13 0h3v6h-3z"
      />
    </svg>
  );
}

interface LibraryBadgesProps {
  library?: LibraryOwnership | null;
  /** Solid pills for use over cover art; otherwise tinted inline pills. */
  overlay?: boolean;
  className?: string;
}

/** "Already in your library" badges, one per owned format. Renders nothing when none. */
export function LibraryBadges({ library, overlay = false, className = '' }: LibraryBadgesProps) {
  const owned = ownedFormats(library);
  if (owned.length === 0) return null;

  const pill = overlay
    ? 'rounded-md border border-sky-700 bg-sky-600 px-1.5 py-0.5 text-[10px] font-bold text-white'
    : 'rounded-md bg-sky-600/15 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700 dark:text-sky-300';
  const style = overlay
    ? { boxShadow: '0 2px 8px rgba(0, 0, 0, 0.4), 0 1px 3px rgba(0, 0, 0, 0.3)' }
    : undefined;

  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {owned.map((format) => (
        <span
          key={format}
          className={`flex items-center gap-0.5 ${pill}`}
          style={style}
          title={isCollectionHolding(library, format) ? COLLECTION_LABELS[format] : LABELS[format]}
          aria-label={
            isCollectionHolding(library, format) ? COLLECTION_LABELS[format] : LABELS[format]
          }
        >
          <FormatIcon format={format} className="h-3 w-3" />
          {isCollectionHolding(library, format) ? `${SHORT[format]} (in set)` : SHORT[format]}
        </span>
      ))}
    </div>
  );
}
