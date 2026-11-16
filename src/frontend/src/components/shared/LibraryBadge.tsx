import type { LibraryOwnership } from '../../types';

/** True when the library check reports this book held in any format. */
export function isInLibrary(library?: LibraryOwnership | null): boolean {
  if (!library) return false;
  return Object.values(library).some((holding) => holding === 'owned' || holding === 'collection');
}

/** True when every format that holds it holds it inside a larger volume. */
export function isCollectionOnly(library?: LibraryOwnership | null): boolean {
  if (!isInLibrary(library)) return false;
  return !Object.values(library ?? {}).includes('owned');
}

interface LibraryBadgeProps {
  library?: LibraryOwnership | null;
  /** Solid pill for use over cover art; otherwise a tinted inline pill. */
  overlay?: boolean;
  className?: string;
}

const FORMATS = [
  { key: 'ebook', name: 'Ebook', short: 'Ebook' },
  { key: 'audiobook', name: 'Audiobook', short: 'Audio' },
] as const;

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

const listNames = (names: string[]) => names.join(' and ').toLowerCase();

/**
 * Badge text and tooltip. When more than one library check is enabled the badge names the
 * format(s) held, so an ebook-only match is not mistaken for an audiobook one; with a single
 * check it stays a plain "In library".
 */
export function libraryBadgeContent(
  library: LibraryOwnership | null | undefined,
  overlay = false,
): { text: string; label: string } | null {
  if (!library || !isInLibrary(library)) return null;

  const collectionOnly = isCollectionOnly(library);
  if (Object.keys(library).length < 2) {
    return collectionOnly
      ? { text: 'In a collection', label: 'In your library, inside a collection' }
      : { text: 'In library', label: 'Already in your library' };
  }

  const held = FORMATS.filter(({ key }) => library[key]);
  const owned = held.filter(({ key }) => library[key] === 'owned').map(({ name }) => name);
  const inCollection = held
    .filter(({ key }) => library[key] === 'collection')
    .map(({ name }) => name);
  const formats = held.map((format) => (overlay ? format.short : format.name)).join(' + ');

  const text = collectionOnly ? `In a collection (${formats})` : formats;
  const label = capitalize(
    [
      owned.length ? `${listNames(owned)} already in your library` : '',
      inCollection.length ? `${listNames(inCollection)} in your library, inside a collection` : '',
    ]
      .filter(Boolean)
      .join('; '),
  );
  return { text, label };
}

/** "Already in your library" badge. Renders nothing when the book is not held. */
export function LibraryBadge({ library, overlay = false, className = '' }: LibraryBadgeProps) {
  const content = libraryBadgeContent(library, overlay);
  if (!content) return null;
  const { text, label } = content;

  const pill = overlay
    ? 'rounded-md border border-sky-700 bg-sky-600 px-1.5 py-0.5 text-[10px] font-bold text-white'
    : 'rounded-md bg-sky-600/15 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700 dark:text-sky-300';
  const style = overlay
    ? { boxShadow: '0 2px 8px rgba(0, 0, 0, 0.4), 0 1px 3px rgba(0, 0, 0, 0.3)' }
    : undefined;

  return (
    <span
      className={`flex w-fit items-center gap-0.5 ${pill} ${className}`}
      style={style}
      title={label}
      aria-label={label}
    >
      <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
      </svg>
      {text}
    </span>
  );
}
