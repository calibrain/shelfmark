import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  Book,
  Release,
  ReleaseSource,
  ReleasesResponse,
  Language,
  StatusData,
  ButtonStateInfo,
  ColumnSchema,
  ReleaseColumnConfig,
  LeadingCellConfig,
  SearchStatusData,
  ContentType,
  RequestPolicyMode,
} from '../types';
import { getReleases, getReleaseSources } from '../services/api';
import { useSocket } from '../contexts/SocketContext';
import { Dropdown } from './Dropdown';
import { DropdownList } from './DropdownList';
import { BookDownloadButton } from './BookDownloadButton';
import { ReleaseCell } from './ReleaseCell';
import { getColorStyleFromHint } from '../utils/colorMaps';
import { getNestedValue } from '../utils/objectHelpers';
import { LanguageMultiSelect } from './LanguageMultiSelect';
import {
  LANGUAGE_OPTION_DEFAULT,
  getLanguageFilterValues,
  getReleaseSearchLanguageParams,
  releaseLanguageMatchesFilter,
  buildLanguageNormalizer,
} from '../utils/languageFilters';
import { getReleaseFormats } from '../utils/releaseFormats';
import { getBookTitleCandidates, getBookAuthorCandidates, sortReleasesByBookMatch } from '../utils/releaseScoring';
import { getCachedReleases, setCachedReleases, invalidateCachedReleases } from '../utils/releaseCache';
import { SortState, getSavedSort, saveSort, clearSort, inferDefaultDirection, sortReleases, FORMAT_SORT_KEY, sortReleasesByFormat } from '../utils/releaseSort';


// Default column configuration (fallback when backend doesn't provide one)
const DEFAULT_COLUMN_CONFIG: ReleaseColumnConfig = {
  columns: [
    {
      key: 'extra.language',
      label: 'Language',
      render_type: 'badge',
      align: 'center',
      width: '60px',
      hide_mobile: false,  // Language shown on mobile
      color_hint: { type: 'map', value: 'language' },
      fallback: '-',
      uppercase: true,
    },
    {
      key: 'format',
      label: 'Format',
      render_type: 'badge',
      align: 'center',
      width: '80px',
      hide_mobile: false,  // Format shown on mobile
      color_hint: { type: 'map', value: 'format' },
      fallback: '-',
      uppercase: true,
    },
    {
      key: 'size',
      label: 'Size',
      render_type: 'size',
      align: 'center',
      width: '80px',
      hide_mobile: false,  // Size shown on mobile
      fallback: '-',
      uppercase: false,
    },
  ],
  grid_template: 'minmax(0,2fr) 60px 80px 80px',
  supported_filters: ['format', 'language'],  // Default: both filters available
};

interface ReleaseModalProps {
  book: Book | null;
  onClose: () => void;
  onDownload: (book: Book, release: Release, contentType: ContentType) => Promise<void>;
  onRequestRelease?: (book: Book, release: Release, contentType: ContentType) => Promise<void>;
  onRequestBook?: (book: Book, contentType: ContentType) => Promise<void>;
  getPolicyModeForSource?: (source: string, contentType: ContentType) => RequestPolicyMode;
  onPolicyRefresh?: () => Promise<unknown>;
  supportedFormats: string[];
  supportedAudiobookFormats?: string[];  // Audiobook formats (m4b, mp3)
  contentType: ContentType;  // 'ebook' or 'audiobook'
  defaultLanguages: string[];
  bookLanguages: Language[];
  currentStatus: StatusData;
  defaultReleaseSource?: string;  // Default tab to show (e.g., 'direct_download')
  onSearchSeries?: (seriesName: string) => void;  // Callback to search for series
  defaultShowManualQuery?: boolean;
  isRequestMode?: boolean;
}


// 5-star rating display with partial fill support
function StarRating({ rating, maxRating = 5 }: { rating: number; maxRating?: number }) {
  // Normalize rating to 0-5 scale if needed
  const normalizedRating = Math.min(Math.max(rating, 0), maxRating);

  return (
    <div className="flex items-center gap-0.5" title={`${rating} out of ${maxRating}`}>
      {[...Array(5)].map((_, index) => {
        const fillPercentage = Math.min(Math.max((normalizedRating - index) * 100, 0), 100);

        return (
          <div key={index} className="relative w-4 h-4">
            {/* Empty star (gray background) */}
            <svg
              className="absolute inset-0 w-4 h-4 text-gray-300 dark:text-gray-600"
              fill="currentColor"
              viewBox="0 0 20 20"
            >
              <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
            </svg>
            {/* Filled star (gold, clipped to fill percentage) */}
            <svg
              className="absolute inset-0 w-4 h-4 text-amber-400"
              fill="currentColor"
              viewBox="0 0 20 20"
              style={{ clipPath: `inset(0 ${100 - fillPercentage}% 0 0)` }}
            >
              <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
            </svg>
          </div>
        );
      })}
    </div>
  );
}

// Thumbnail component for release rows
const ReleaseThumbnail = ({ preview, title }: { preview?: string; title?: string }) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  if (!preview || imageError) {
    return (
      <div
        className="w-7 h-10 sm:w-8 sm:h-12 rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-[7px] sm:text-[8px] font-medium text-gray-500 dark:text-gray-400 flex-shrink-0"
        aria-label="No cover available"
      >
        No Cover
      </div>
    );
  }

  return (
    <div className="relative w-7 h-10 sm:w-8 sm:h-12 rounded overflow-hidden bg-gray-100 dark:bg-gray-800 border border-white/40 dark:border-gray-700/70 flex-shrink-0">
      {!imageLoaded && (
        <div className="absolute inset-0 bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
      )}
      <img
        src={preview}
        alt={title || 'Book cover'}
        className="w-full h-full object-cover object-top"
        loading="lazy"
        onLoad={() => setImageLoaded(true)}
        onError={() => setImageError(true)}
        style={{ opacity: imageLoaded ? 1 : 0, transition: 'opacity 0.2s ease-in-out' }}
      />
    </div>
  );
};

// Leading cell component - shows thumbnail, badge, or nothing based on config
const LeadingCell = ({
  config,
  release
}: {
  config?: LeadingCellConfig;
  release: Release;
}) => {
  // Default to thumbnail mode if no config
  const cellType = config?.type || 'thumbnail';

  if (cellType === 'none') {
    return null;
  }

  if (cellType === 'thumbnail') {
    const key = config?.key || 'extra.preview';
    const preview = getNestedValue(release as unknown as Record<string, unknown>, key) as string | undefined;
    return <ReleaseThumbnail preview={preview} title={release.title} />;
  }

  // Badge type
  if (cellType === 'badge' && config?.key) {
    const value = getNestedValue(release as unknown as Record<string, unknown>, config.key);
    const displayValue = value ? String(value) : '';
    const colorStyle = getColorStyleFromHint(displayValue, config.color_hint);
    const text = config.uppercase ? displayValue.toUpperCase() : displayValue;

    return (
      <div
        className={`w-7 h-10 sm:w-8 sm:h-12 rounded-lg ${colorStyle.bg} flex items-center justify-center flex-shrink-0`}
      >
        <span className={`text-[8px] sm:text-[9px] font-bold ${colorStyle.text} text-center leading-tight px-0.5`}>
          {text}
        </span>
      </div>
    );
  }

  // Fallback
  return <ReleaseThumbnail preview={undefined} title={release.title} />;
};

// Release row component with dynamic columns
const ReleaseRow = ({
  release,
  index,
  onDownload,
  buttonState,
  columns,
  gridTemplate,
  leadingCell,
  onlineServers,
}: {
  release: Release;
  index: number;
  onDownload: () => Promise<void>;
  buttonState: ButtonStateInfo;
  columns: ColumnSchema[];
  gridTemplate: string;
  leadingCell?: LeadingCellConfig;
  onlineServers?: string[];
}) => {
  const author = release.extra?.author as string | undefined;

  // Filter columns visible on mobile
  const mobileColumns = columns.filter((c) => !c.hide_mobile);

  // Determine if leading cell should be shown
  // Default to showing thumbnail if no config provided, hide only if explicitly set to 'none'
  const showLeadingCell = leadingCell?.type !== 'none';

  // Build grid template based on whether leading cell is shown
  const desktopGridTemplate = showLeadingCell
    ? `auto ${gridTemplate} auto`
    : `${gridTemplate} auto`;

  const mobileGridTemplate = showLeadingCell
    ? 'auto 1fr auto'
    : '1fr auto';

  return (
    <div
      className="pl-5 pr-4 sm:pr-5 py-2 transition-colors duration-200 hover-row animate-pop-up will-change-transform"
      style={{
        animationDelay: `${index * 30}ms`,
        animationFillMode: 'both',
      }}
    >
      {/* Desktop layout with dynamic grid */}
      <div
        className="hidden sm:grid items-center gap-3"
        style={{ gridTemplateColumns: desktopGridTemplate }}
      >
        {/* Leading cell: Thumbnail, Badge, or nothing */}
        {showLeadingCell && <LeadingCell config={leadingCell} release={release} />}

        {/* Fixed: Title and author */}
        <div className="min-w-0">
          <p className="text-sm font-medium line-clamp-2" title={release.title}>
            {release.info_url ? (
              <a
                href={release.info_url}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-emerald-600 dark:hover:text-emerald-400 hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {release.title}
              </a>
            ) : (
              release.title
            )}
          </p>
          {author && (
            <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
              {author}
            </p>
          )}
        </div>

        {/* Dynamic columns from schema */}
        {columns.map((col) => (
          <ReleaseCell key={col.key} column={col} release={release} onlineServers={onlineServers} />
        ))}

        {/* Fixed: Action button */}
        <BookDownloadButton
          buttonState={buttonState}
          onDownload={onDownload}
          variant="icon"
          size="sm"
          ariaLabel={`${buttonState.text} ${release.title}`}
        />
      </div>

      {/* Mobile layout - author inline with title, info line below */}
      <div
        className="grid sm:hidden items-center gap-2"
        style={{ gridTemplateColumns: mobileGridTemplate }}
      >
        {/* Leading cell: Thumbnail, Badge, or nothing */}
        {showLeadingCell && <LeadingCell config={leadingCell} release={release} />}

        <div className="min-w-0">
          {/* Title and author on same line */}
          <p className="text-sm leading-tight line-clamp-2" title={release.title}>
            {release.info_url ? (
              <a
                href={release.info_url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium hover:text-emerald-600 dark:hover:text-emerald-400 hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {release.title}
              </a>
            ) : (
              <span className="font-medium">{release.title}</span>
            )}
            {author && (
              <span className="text-gray-500 dark:text-gray-400 font-normal"> — {author}</span>
            )}
          </p>
          {/* Plugin-provided info line (format, size, indexer, seeders, etc.) */}
          {mobileColumns.length > 0 && (
            <div className="flex items-center gap-1.5 mt-1 text-[10px] text-gray-500 dark:text-gray-400">
              {(() => {
                // Pre-filter columns that will render content to avoid orphan dots
                const columnsWithContent = mobileColumns.filter((col) => {
                  const rawValue = getNestedValue(release as unknown as Record<string, unknown>, col.key);
                  const value = rawValue !== undefined && rawValue !== null ? String(rawValue) : col.fallback;

                  if (col.render_type === 'flag_icon') {
                    // flag_icon returns null in compact mode when empty
                    if (!value || value === col.fallback) {
                      return false;
                    }
                  }

                  if (col.render_type === 'tags') {
                    // tags render null in compact mode when empty and no fallback
                    if (Array.isArray(rawValue)) {
                      return rawValue.some((tag) => String(tag).trim()) || Boolean(col.fallback);
                    }
                    if (!value || value === col.fallback) {
                      return Boolean(col.fallback);
                    }
                  }

                  return true;
                });
                return columnsWithContent.map((col, idx) => (
                  <span key={col.key} className="flex items-center gap-1.5">
                    {idx > 0 && <span className="text-gray-300 dark:text-gray-600">·</span>}
                    <ReleaseCell column={col} release={release} compact onlineServers={onlineServers} />
                  </span>
                ));
              })()}
            </div>
          )}
        </div>

        <BookDownloadButton
          buttonState={buttonState}
          onDownload={onDownload}
          variant="icon"
          size="sm"
          ariaLabel={`${buttonState.text} ${release.title}`}
        />
      </div>
    </div>
  );
};

// Shimmer block with wave animation - same as DownloadsSidebar
function ShimmerBlock({ className }: { className: string }) {
  return (
    <div
      className={`rounded bg-gray-200 dark:bg-gray-800 relative overflow-hidden ${className}`}
    >
      <div
        className="absolute inset-0 dark:opacity-50"
        style={{
          background: 'linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.4) 50%, transparent 100%)',
          backgroundSize: '200% 100%',
          animation: 'wave 2s ease-in-out infinite',
        }}
      />
    </div>
  );
}

// Loading skeleton for releases - matches ReleaseRow layout
function ReleaseSkeleton() {
  return (
    <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
      {[1, 2, 3, 4, 5].map((i) => (
        <div
          key={i}
          className="px-5 py-2"
          style={{
            opacity: 1 - (i - 1) * 0.15, // Fade out lower rows
          }}
        >
          <div className="grid grid-cols-[auto_1fr_auto] sm:grid-cols-[auto_minmax(0,2fr)_60px_80px_80px_auto] items-center gap-2 sm:gap-3">
            {/* Thumbnail skeleton */}
            <ShimmerBlock className="w-7 h-10 sm:w-10 sm:h-14" />

            {/* Title and author skeleton */}
            <div className="min-w-0 space-y-1.5">
              <ShimmerBlock className="h-4 w-3/4" />
              <ShimmerBlock className="h-3 w-1/2" />
            </div>

            {/* Language badge skeleton - desktop */}
            <div className="hidden sm:flex justify-center">
              <ShimmerBlock className="w-8 h-5" />
            </div>

            {/* Format badge skeleton - desktop */}
            <div className="hidden sm:flex justify-center">
              <ShimmerBlock className="w-12 h-5" />
            </div>

            {/* Size skeleton - desktop */}
            <div className="hidden sm:flex justify-center">
              <ShimmerBlock className="w-14 h-4" />
            </div>

            {/* Mobile info + action skeleton */}
            <div className="flex items-center gap-2">
              {/* Mobile: format + size inline */}
              <div className="flex sm:hidden flex-col items-end gap-1">
                <ShimmerBlock className="w-10 h-3" />
                <ShimmerBlock className="w-14 h-3" />
              </div>

              {/* Action button skeleton */}
              <ShimmerBlock className="w-8 h-8 !rounded-full" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// Empty state component
function EmptyState({ message }: { message: string }) {
  return (
    <div className="text-center py-12 px-4">
      <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-gray-100 dark:bg-gray-800 mb-4">
        <svg
          className="w-7 h-7 text-gray-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
          />
        </svg>
      </div>
      <p className="text-sm text-gray-500 dark:text-gray-400">{message}</p>
    </div>
  );
}

// Error state component
function ErrorState({ message }: { message: string }) {
  return (
    <div className="text-center py-12 px-4">
      <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-red-100 dark:bg-red-900/30 mb-4">
        <svg
          className="w-7 h-7 text-red-600 dark:text-red-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z"
          />
        </svg>
      </div>
      <h4 className="text-base font-semibold text-gray-900 dark:text-gray-100 mb-2">
        Error Loading Releases
      </h4>
      <p className="text-sm text-gray-500 dark:text-gray-400 max-w-xs mx-auto">{message}</p>
    </div>
  );
}


export const ReleaseModal = ({
  book,
  onClose,
  onDownload,
  onRequestRelease,
  onRequestBook,
  getPolicyModeForSource,
  onPolicyRefresh,
  supportedFormats,
  supportedAudiobookFormats = [],
  contentType = 'ebook',
  defaultLanguages,
  bookLanguages,
  currentStatus,
  defaultReleaseSource,
  onSearchSeries,
  defaultShowManualQuery = false,
  isRequestMode = false,
}: ReleaseModalProps) => {
  // Use audiobook formats when in audiobook mode
  const effectiveFormats = contentType === 'audiobook' && supportedAudiobookFormats.length > 0
    ? supportedAudiobookFormats
    : supportedFormats;
  const isDirectProviderContext = (book?.provider || '').toLowerCase() === 'direct_download';
  const [isClosing, setIsClosing] = useState(false);
  const [isRequestingBook, setIsRequestingBook] = useState(false);

  // Available sources from plugin registry
  const [availableSources, setAvailableSources] = useState<ReleaseSource[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(true);

  // Active tab (source name)
  const [activeTab, setActiveTab] = useState<string>('');

  // Track if book summary has scrolled out of view
  const [showHeaderThumb, setShowHeaderThumb] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bookSummaryRef = useRef<HTMLDivElement>(null);

  // Releases data per source
  const [releasesBySource, setReleasesBySource] = useState<Record<string, ReleasesResponse | null>>({});
  const [loadingBySource, setLoadingBySource] = useState<Record<string, boolean>>({});
  const [errorBySource, setErrorBySource] = useState<Record<string, string | null>>({});
  const [expandedBySource, setExpandedBySource] = useState<Record<string, boolean>>({});

  // Search status from WebSocket (for showing progress during slow searches like IRC)
  const [searchStatus, setSearchStatus] = useState<SearchStatusData | null>(null);
  const { socket } = useSocket();
  const lastStatusTimeRef = useRef<number>(0);
  const pendingStatusRef = useRef<SearchStatusData | null>(null);
  const statusTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Filters - initialized from config settings
  // Empty string means "show all supported formats" (filtered by supportedFormats)
  // A specific value means "show only that format"
  const [formatFilter, setFormatFilter] = useState<string>('');
  const [languageFilter, setLanguageFilter] = useState<string[]>([LANGUAGE_OPTION_DEFAULT]);
  // Indexer filter - empty array means "show all", otherwise show only selected indexers
  const [indexerFilter, setIndexerFilter] = useState<string[]>([]);
  // Track which tabs have had indexer filter initialized (to avoid overriding user changes)
  const indexerFilterInitializedRef = useRef<Set<string>>(new Set());
  const [manualQuery, setManualQuery] = useState<string>('');
  const [showManualQuery, setShowManualQuery] = useState<boolean>(false);

  // Sort state - keyed by source name, persisted to localStorage
  // null means "Default" (best title match), undefined means "not set yet"
  const [sortBySource, setSortBySource] = useState<Record<string, SortState | null>>({});
  const [formatSortExpanded, setFormatSortExpanded] = useState(false);

  // Description expansion
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const [descriptionOverflows, setDescriptionOverflows] = useState(false);
  const descriptionRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (!book || !onPolicyRefresh) return;
    void onPolicyRefresh();
  }, [book?.id, onPolicyRefresh]);

  // Close handler with animation
  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [onClose]);

  const handleRequestBook = useCallback(async (): Promise<void> => {
    if (!book || !onRequestBook || isRequestingBook) {
      return;
    }
    setIsRequestingBook(true);
    try {
      await onRequestBook(book, contentType);
      handleClose();
    } finally {
      setIsRequestingBook(false);
    }
  }, [book, onRequestBook, isRequestingBook, contentType, handleClose]);

  // Handle ESC key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [handleClose]);

  // Body scroll lock
  useEffect(() => {
    if (book) {
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }
  }, [book]);

  // Reset modal state when book changes to prevent stale data
  useEffect(() => {
    setDescriptionExpanded(false);
    setDescriptionOverflows(false);
    setShowHeaderThumb(false);
    setReleasesBySource({});
    setLoadingBySource({});
    setErrorBySource({});
    setExpandedBySource({});
    setFormatFilter('');
    setLanguageFilter([LANGUAGE_OPTION_DEFAULT]);
    setIndexerFilter([]);
    indexerFilterInitializedRef.current = new Set();
    const baseTitle = book?.search_title || book?.title || '';
    const baseAuthor = book?.search_author || book?.author || '';
    const defaultQuery = `${baseTitle} ${baseAuthor}`.trim();
    setManualQuery(defaultShowManualQuery ? defaultQuery : '');
    setShowManualQuery(defaultShowManualQuery);
    setSearchStatus(null);
    lastStatusTimeRef.current = 0;
    pendingStatusRef.current = null;
    if (statusTimeoutRef.current) {
      clearTimeout(statusTimeoutRef.current);
      statusTimeoutRef.current = null;
    }
  }, [book?.id, defaultShowManualQuery, book?.search_title, book?.title, book?.search_author, book?.author]);

  // Set up WebSocket listener for search status updates
  useEffect(() => {
    if (!book || !socket) return;

    const MIN_DISPLAY_TIME = 1500; // Minimum ms to show each status message

    const handleSearchStatus = (data: SearchStatusData) => {
      // Only handle status for the current active tab
      if (data.source !== activeTab) return;

      const now = Date.now();
      const elapsed = now - lastStatusTimeRef.current;

      // If enough time has passed, update immediately
      if (elapsed >= MIN_DISPLAY_TIME) {
        setSearchStatus(data);
        lastStatusTimeRef.current = now;
        pendingStatusRef.current = null;
      } else {
        // Queue the update for later
        pendingStatusRef.current = data;

        // Clear any existing timeout
        if (statusTimeoutRef.current) {
          clearTimeout(statusTimeoutRef.current);
        }

        // Schedule update after remaining time
        statusTimeoutRef.current = setTimeout(() => {
          if (pendingStatusRef.current) {
            setSearchStatus(pendingStatusRef.current);
            lastStatusTimeRef.current = Date.now();
            pendingStatusRef.current = null;
          }
        }, MIN_DISPLAY_TIME - elapsed);
      }
    };

    socket.on('search_status', handleSearchStatus);

    return () => {
      socket.off('search_status', handleSearchStatus);
      if (statusTimeoutRef.current) {
        clearTimeout(statusTimeoutRef.current);
      }
    };
  }, [book, socket, activeTab]);

  // Clear search status when loading finishes
  useEffect(() => {
    if (!loadingBySource[activeTab]) {
      setSearchStatus(null);
    }
  }, [loadingBySource, activeTab]);

  // Initialize indexer filter from default_indexers when results first load for a tab
  useEffect(() => {
    const response = releasesBySource[activeTab];
    if (!response?.column_config) return;

    // Only initialize once per tab per book
    if (indexerFilterInitializedRef.current.has(activeTab)) return;

    const defaultIndexers = response.column_config.default_indexers;
    if (defaultIndexers && defaultIndexers.length > 0) {
      setIndexerFilter(defaultIndexers);
    }
    // Mark as initialized even if no default_indexers (to avoid re-checking)
    indexerFilterInitializedRef.current.add(activeTab);
  }, [releasesBySource, activeTab]);

  // Check if description text overflows (needs "more" button)
  useEffect(() => {
    const el = descriptionRef.current;
    if (el && !descriptionExpanded) {
      // Compare scrollHeight to clientHeight to detect overflow
      setDescriptionOverflows(el.scrollHeight > el.clientHeight);
    }
  }, [book?.description, descriptionExpanded]);

  // Tab indicator refs and state for sliding animation
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [tabIndicatorStyle, setTabIndicatorStyle] = useState({ left: 0, width: 0 });

  // Track scroll to show/hide header thumbnail
  useEffect(() => {
    const scrollContainer = scrollContainerRef.current;
    const bookSummary = bookSummaryRef.current;
    if (!scrollContainer || !bookSummary) return;

    const handleScroll = () => {
      const summaryRect = bookSummary.getBoundingClientRect();
      const containerRect = scrollContainer.getBoundingClientRect();
      // Show header thumb when the book summary section has scrolled past the top
      setShowHeaderThumb(summaryRect.bottom < containerRect.top + 20);
    };

    scrollContainer.addEventListener('scroll', handleScroll, { passive: true });
    return () => scrollContainer.removeEventListener('scroll', handleScroll);
  }, [book]);

  // Fetch available sources on mount
  useEffect(() => {
    if (!book) return;

    const fetchSources = async () => {
      try {
        setSourcesLoading(true);
        const sources = await getReleaseSources();
        const modalSources = isDirectProviderContext
          ? sources.filter((source) => source.name === 'direct_download')
          : sources;
        setAvailableSources(modalSources);

        // Filter sources by content type support
        const supportedSources = modalSources.filter(s => {
          const types = s.supported_content_types || ['ebook', 'audiobook'];
          return types.includes(contentType);
        });

        if (isDirectProviderContext) {
          if (supportedSources.some((source) => source.name === 'direct_download')) {
            setActiveTab('direct_download');
          } else {
            setActiveTab('');
          }
          return;
        }

        // Set active tab: prefer defaultReleaseSource if enabled and supports content type
        if (supportedSources.length > 0) {
          const enabledSources = supportedSources.filter(s => s.enabled);
          const defaultIsEnabled = defaultReleaseSource &&
            enabledSources.some(s => s.name === defaultReleaseSource);

          let defaultSource: string;
          if (defaultIsEnabled) {
            defaultSource = defaultReleaseSource;
          } else if (enabledSources.length > 0) {
            defaultSource = enabledSources[0].name;
          } else {
            defaultSource = supportedSources[0].name;  // Fallback to first supported source
          }
          setActiveTab(defaultSource);
        } else if (sources.length > 0) {
          // No sources support this content type - fall back to first source
          setActiveTab(sources[0].name);
        }
      } catch (err) {
        console.error('Failed to fetch release sources:', err);
        // Fallback: assume direct_download is available (for ebooks)
        setAvailableSources([{
          name: 'direct_download',
          display_name: "Direct Download",
          enabled: true,
          supported_content_types: ['ebook']
        }]);
        if (contentType === 'ebook') {
          setActiveTab('direct_download');
        }
      } finally {
        setSourcesLoading(false);
      }
    };

    fetchSources();
  }, [book, defaultReleaseSource, contentType, isDirectProviderContext]);

  // Fetch releases when active tab changes (with caching)
  // Initial fetch always uses ISBN-first search; expansion is handled by handleExpandSearch
  useEffect(() => {
    if (!book || !activeTab || !book.provider || !book.provider_id) return;

    // Extract to local variables for TypeScript narrowing
    const provider = book.provider;
    const bookId = book.provider_id;

    // Skip if already loaded, currently loading, or has error (prevents retry loop)
    if (releasesBySource[activeTab] !== undefined || loadingBySource[activeTab] || errorBySource[activeTab]) return;

    // Check module-level cache first
    const cached = getCachedReleases(provider, bookId, activeTab, contentType);
    if (cached) {
      setReleasesBySource((prev) => ({ ...prev, [activeTab]: cached }));
      return;
    }

    const fetchReleases = async () => {
      setLoadingBySource((prev) => ({ ...prev, [activeTab]: true }));
      setErrorBySource((prev) => ({ ...prev, [activeTab]: null }));

      try {
        const response = await getReleases(provider, bookId, activeTab, book.title, book.author, undefined, undefined, contentType, manualQuery.trim() || undefined);
        setCachedReleases(provider, bookId, activeTab, contentType, response);
        setReleasesBySource((prev) => ({ ...prev, [activeTab]: response }));
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to fetch releases';
        setErrorBySource((prev) => ({ ...prev, [activeTab]: message }));
      } finally {
        setLoadingBySource((prev) => ({ ...prev, [activeTab]: false }));
      }
    };

    fetchReleases();
  }, [book, activeTab, releasesBySource, loadingBySource, errorBySource, contentType, manualQuery]);

  // Handler for expanding search (title+author instead of ISBN)
  // Fetches additional results and merges with existing ISBN results
  const handleExpandSearch = useCallback(async () => {
    if (!activeTab || !book?.provider || !book?.provider_id) return;

    const provider = book.provider;
    const bookId = book.provider_id;

    // Mark as loading and expanded
    setLoadingBySource((prev) => ({ ...prev, [activeTab]: true }));
    setExpandedBySource((prev) => ({ ...prev, [activeTab]: true }));

    try {
      // Resolve language codes for the API call (same logic as Apply button)
      const languagesParam = getReleaseSearchLanguageParams(languageFilter, bookLanguages, defaultLanguages);

      // Pass indexer filter only if the source supports it (empty array = search all)
      const supportsIndexerFilter = releasesBySource[activeTab]?.column_config?.supported_filters?.includes('indexer');
      const indexersParam = supportsIndexerFilter && indexerFilter.length > 0 ? indexerFilter : undefined;

      // Fetch with expand_search=true (title+author search)
      const expandedResponse = await getReleases(
        provider, bookId, activeTab, book.title, book.author, true, languagesParam, contentType, manualQuery.trim() || undefined, indexersParam
      );

      // Merge with existing results, deduplicating by source_id
      setReleasesBySource((prev) => {
        const existing = prev[activeTab];
        if (!existing) {
          return { ...prev, [activeTab]: expandedResponse };
        }

        const seenIds = new Set(existing.releases.map(r => r.source_id));
        const newReleases = expandedResponse.releases.filter(r => !seenIds.has(r.source_id));

        return {
          ...prev,
          [activeTab]: {
            ...existing,
            releases: [...existing.releases, ...newReleases],
          },
        };
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to expand search';
      setErrorBySource((prev) => ({ ...prev, [activeTab]: message }));
    } finally {
      setLoadingBySource((prev) => ({ ...prev, [activeTab]: false }));
    }
  }, [activeTab, book, languageFilter, bookLanguages, defaultLanguages, contentType, manualQuery, indexerFilter, releasesBySource]);

  // Build list of tabs to show
  // Only show enabled sources that support the current content type
  // Order: default source first, then other enabled sources
  const allTabs = useMemo(() => {
    type TabInfo = { name: string; displayName: string; enabled: boolean };

    const enabledTabs: TabInfo[] = [];

    // Filter to only enabled sources that support this content type
    availableSources.forEach((src) => {
      const allowDisabledDirectTab = isDirectProviderContext && src.name === 'direct_download';
      // Skip disabled sources entirely, except direct tab in direct-provider context.
      if (!src.enabled && !allowDisabledDirectTab) {
        return;
      }

      // Check if source supports the current content type
      const supportedTypes = src.supported_content_types || ['ebook', 'audiobook'];
      if (!supportedTypes.includes(contentType)) {
        return;
      }

      enabledTabs.push({ name: src.name, displayName: src.display_name, enabled: true });
    });

    // Sort so default source appears first
    if (defaultReleaseSource) {
      enabledTabs.sort((a, b) => {
        if (a.name === defaultReleaseSource) return -1;
        if (b.name === defaultReleaseSource) return 1;
        return 0;
      });
    }

    return enabledTabs;
  }, [availableSources, defaultReleaseSource, contentType, isDirectProviderContext]);

  // Update tab indicator position when active tab changes
  useEffect(() => {
    const activeButton = tabRefs.current[activeTab];
    if (activeButton) {
      // Get position relative to parent container
      const containerRect = activeButton.parentElement?.getBoundingClientRect();
      const buttonRect = activeButton.getBoundingClientRect();
      if (containerRect) {
        setTabIndicatorStyle({
          left: buttonRect.left - containerRect.left,
          width: buttonRect.width,
        });
      }
    }
  }, [activeTab, allTabs]);

  // Get unique formats from current releases for filter dropdown
  // Only show formats that are in the supported list
  const availableFormats = useMemo(() => {
    const releases = releasesBySource[activeTab]?.releases || [];
    const formats = new Set<string>();
    const effectiveLower = new Set(effectiveFormats.map((f) => f.toLowerCase()));

    releases.forEach((r) => {
      const releaseFormats = getReleaseFormats(r);
      releaseFormats.forEach((fmt) => {
        // Only include formats that are in the supported list
        if (effectiveLower.has(fmt)) {
          formats.add(fmt);
        }
      });
    });
    return Array.from(formats).sort();
  }, [releasesBySource, activeTab, effectiveFormats]);

  // Build select options for format filter
  const formatOptions = useMemo(() => {
    const options = [{ value: '', label: 'All Formats' }];
    availableFormats.forEach((fmt) => {
      options.push({ value: fmt, label: fmt.toUpperCase() });
    });
    return options;
  }, [availableFormats]);

  // Get available indexers for filter dropdown
  // Prefer available_indexers from column config (all enabled Prowlarr indexers),
  // fall back to unique indexers from results if not provided
  const availableIndexers = useMemo(() => {
    // Use column config's available_indexers if provided (e.g., from Prowlarr)
    const configIndexers = releasesBySource[activeTab]?.column_config?.available_indexers;
    if (configIndexers && configIndexers.length > 0) {
      return configIndexers;
    }
    // Fall back to indexers found in results
    const releases = releasesBySource[activeTab]?.releases || [];
    const indexers = new Set<string>();
    releases.forEach((r) => {
      if (r.indexer) {
        indexers.add(r.indexer);
      }
    });
    return Array.from(indexers).sort();
  }, [releasesBySource, activeTab]);

  // Resolve language filter to actual language codes for filtering
  const resolvedLanguageCodes = useMemo(() => {
    return getLanguageFilterValues(languageFilter, bookLanguages, defaultLanguages);
  }, [languageFilter, bookLanguages, defaultLanguages]);

  // Build language normalizer for release filtering (handles both codes like "en" and names like "English")
  const languageNormalizer = useMemo(() => {
    return buildLanguageNormalizer(bookLanguages);
  }, [bookLanguages]);

  // Get column config from response or use default (moved before filteredReleases for sorting)
  const columnConfig = useMemo((): ReleaseColumnConfig => {
    const response = releasesBySource[activeTab];
    if (response?.column_config) {
      return response.column_config;
    }
    return DEFAULT_COLUMN_CONFIG;
  }, [releasesBySource, activeTab]);

  // Get sortable columns from column config
  const sortableColumns = useMemo(() => {
    return columnConfig.columns.filter(col => col.sortable) || [];
  }, [columnConfig]);

  // Build unified list of all sort options (from sortable columns + extra_sort_options)
  const allSortOptions = useMemo(() => {
    const fromColumns = sortableColumns.map(col => ({
      label: col.label,
      sortKey: col.sort_key || col.key,
      defaultDirection: inferDefaultDirection(col.render_type) as 'asc' | 'desc',
    }));
    const fromExtra = (columnConfig.extra_sort_options || []).map(opt => ({
      label: opt.label,
      sortKey: opt.sort_key,
      defaultDirection: 'desc' as const,  // Extra sort options are typically numeric (e.g., peers)
    }));
    return [...fromColumns, ...fromExtra];
  }, [sortableColumns, columnConfig.extra_sort_options]);

  const isValidSortForCurrentResults = useCallback((sort: SortState | null): boolean => {
    if (!sort) return false;

    if (sort.key === FORMAT_SORT_KEY) {
      return !!sort.value && availableFormats.includes(sort.value);
    }

    return allSortOptions.some(opt => opt.sortKey === sort.key);
  }, [availableFormats, allSortOptions]);

  // Get current sort state for active tab (from state, localStorage, or default to null = best match)
  const currentSort = useMemo((): SortState | null => {
    // Check state first - explicit null means "Default" was selected
    if (activeTab in sortBySource) {
      const inMemory = sortBySource[activeTab];
      return inMemory === null || isValidSortForCurrentResults(inMemory) ? inMemory : null;
    }
    // Check localStorage
    const saved = getSavedSort(activeTab);
    if (isValidSortForCurrentResults(saved)) {
      return saved;
    }
    // Default to null (best-match sorting)
    return null;
  }, [activeTab, sortBySource, isValidSortForCurrentResults]);

  // Handle sort change - null means "Default" (best title match), otherwise toggle direction or set new column
  const handleSortChange = useCallback((sortKey: string | null, defaultDirection: 'asc' | 'desc', value?: string) => {
    if (sortKey === null) {
      // "Default" selected - use best-match sorting
      setSortBySource(prev => {
        const next = { ...prev };
        delete next[activeTab];
        return next;
      });
      clearSort(activeTab);
      return;
    }

    const currentState = sortBySource[activeTab] ?? currentSort;
    let newState: SortState;

    const isSameSort = currentState && currentState.key === sortKey && currentState.value === value;
    if (isSameSort) {
      // Same key+value - toggle direction
      newState = {
        key: sortKey,
        direction: currentState.direction === 'asc' ? 'desc' : 'asc',
        ...(value !== undefined && { value }),
      };
    } else {
      // New key or different value - use provided default direction
      newState = {
        key: sortKey,
        direction: defaultDirection,
        ...(value !== undefined && { value }),
      };
    }

    setSortBySource(prev => ({ ...prev, [activeTab]: newState }));
    saveSort(activeTab, newState);
  }, [activeTab, sortBySource, currentSort]);

  // Filter and sort releases based on settings and user selection
  const filteredReleases = useMemo(() => {
    const releases = releasesBySource[activeTab]?.releases || [];
    const effectiveLower = new Set(effectiveFormats.map((f) => f.toLowerCase()));
    const supportsIndexerFilter = columnConfig.supported_filters?.includes('indexer');
    const selectedFormat = formatFilter.toLowerCase();

    // First, filter
    let filtered = releases.filter((r) => {
      // Format filtering
      const releaseFormats = getReleaseFormats(r);

      if (formatFilter) {
        // User selected a specific format - match if any format on the release matches
        if (!releaseFormats.includes(selectedFormat)) return false;
      } else if (releaseFormats.length > 0) {
        // No specific filter - show only releases that include at least one supported format
        if (!releaseFormats.some((fmt) => effectiveLower.has(fmt))) return false;
      }
      // Releases with no format info pass through when no filter is set (show all)

      // Language filtering - use r.language when provided by enriched indexers
      // Releases with no language (null/undefined) always pass
      const releaseLang = r.language as string | undefined;
      if (!releaseLanguageMatchesFilter(releaseLang, resolvedLanguageCodes ?? defaultLanguages, languageNormalizer)) {
        return false;
      }

      // Indexer filtering - empty array means show all
      if (supportsIndexerFilter && indexerFilter.length > 0 && r.indexer) {
        if (!indexerFilter.includes(r.indexer)) {
          return false;
        }
      }

      return true;
    });

    // Then, sort by explicit column/format, or default to book-title relevance with exact author boost
    if (currentSort?.key === FORMAT_SORT_KEY && currentSort.value) {
      filtered = sortReleasesByFormat(filtered, currentSort.value, currentSort.direction);
    } else if (currentSort && allSortOptions.length > 0) {
      filtered = sortReleases(filtered, currentSort.key, currentSort.direction);
    } else {
      const responseBook = releasesBySource[activeTab]?.book;
      const titleCandidates = getBookTitleCandidates(book, responseBook);
      const authorCandidates = getBookAuthorCandidates(book, responseBook);
      filtered = sortReleasesByBookMatch(filtered, titleCandidates, authorCandidates);
    }

    return filtered;
  }, [releasesBySource, activeTab, formatFilter, resolvedLanguageCodes, effectiveFormats, defaultLanguages, languageNormalizer, indexerFilter, currentSort, allSortOptions, columnConfig, book]);

  // Pre-compute display field lookups to avoid repeated .find() calls in JSX
  const displayFields = useMemo(() => {
    if (!book?.display_fields) return null;

    const starField = book.display_fields.find(f => f.icon === 'star');
    const ratingsField = book.display_fields.find(f => f.icon === 'ratings');
    const usersField = book.display_fields.find(f => f.icon === 'users');
    const pagesField = book.display_fields.find(f => f.icon === 'book');

    return { starField, ratingsField, usersField, pagesField };
  }, [book?.display_fields]);

  const getReleaseActionMode = useCallback(
    (release: Release): RequestPolicyMode => {
      if (!getPolicyModeForSource) {
        return 'download';
      }
      return getPolicyModeForSource(release.source, contentType);
    },
    [getPolicyModeForSource, contentType]
  );

  // Get button state for a release row (queue state + policy mode).
  const getButtonState = useCallback(
    (release: Release): ButtonStateInfo => {
      const releaseId = release.source_id;
      // Check error first
      if (currentStatus.error && currentStatus.error[releaseId]) {
        return { text: 'Failed', state: 'error' };
      }
      // Check completed
      if (currentStatus.complete && currentStatus.complete[releaseId]) {
        return { text: 'Downloaded', state: 'complete' };
      }
      // Check in-progress states
      if (currentStatus.downloading && currentStatus.downloading[releaseId]) {
        const book = currentStatus.downloading[releaseId];
        return {
          text: 'Downloading',
          state: 'downloading',
          progress: book.progress,
        };
      }
      if (currentStatus.locating && currentStatus.locating[releaseId]) {
        return { text: 'Locating files', state: 'locating' };
      }
      if (currentStatus.resolving && currentStatus.resolving[releaseId]) {
        return { text: 'Resolving', state: 'resolving' };
      }
      if (currentStatus.queued && currentStatus.queued[releaseId]) {
        return { text: 'Queued', state: 'queued' };
      }

      const mode = getReleaseActionMode(release);
      if (mode === 'request_release') {
        return { text: 'Request', state: 'download' };
      }
      if (mode === 'blocked' || mode === 'request_book') {
        return { text: 'Unavailable', state: 'blocked' };
      }
      return { text: 'Download', state: 'download' };
    },
    [currentStatus, getReleaseActionMode]
  );

  // Handle row action based on resolved policy mode.
  const handleReleaseAction = useCallback(
    async (release: Release): Promise<void> => {
      if (!book) {
        return;
      }

      const mode = getReleaseActionMode(release);
      if (mode === 'download') {
        await onDownload(book, release, contentType);
        handleClose();
        return;
      }
      if (mode === 'request_release') {
        if (onRequestRelease) {
          await onRequestRelease(book, release, contentType);
          handleClose();
        }
        return;
      }
      // blocked / request_book — should not be reachable (button is disabled),
      // but guard defensively.
    },
    [book, getReleaseActionMode, onDownload, onRequestRelease, contentType, handleClose]
  );

  if (!book && !isClosing) return null;
  if (!book) return null;

  const titleId = `release-modal-title-${book.id}`;
  const providerDisplay =
    book.provider_display_name ||
    (book.provider ? book.provider.charAt(0).toUpperCase() + book.provider.slice(1) : 'Unknown');

  const currentTabLoading = loadingBySource[activeTab] ?? false;
  const currentTabError = errorBySource[activeTab] ?? null;
  const isInitialLoading = currentTabLoading || (releasesBySource[activeTab] === undefined && !currentTabError);

  const modal = (
    <div
      className="modal-overlay active sm:px-6 sm:py-6"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className={`details-container w-full max-w-3xl h-full sm:h-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-full sm:h-[90vh] sm:max-h-[90vh] flex-col overflow-hidden rounded-none sm:rounded-2xl border-0 sm:border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-none sm:shadow-2xl">
          {/* Header */}
          <header className="flex items-start gap-3 border-b border-[var(--border-muted)] px-5 py-4">
            {/* Animated thumbnail that appears when scrolling */}
            <div
              className="flex-shrink-0 overflow-hidden transition-[width,margin] duration-300 ease-out"
              style={{
                width: showHeaderThumb ? 46 : 0,
                marginRight: showHeaderThumb ? 0 : -12,
              }}
            >
              <div
                className="transition-opacity duration-300 ease-out"
                style={{ opacity: showHeaderThumb ? 1 : 0 }}
              >
                {book.preview ? (
                  <img
                    src={book.preview}
                    alt=""
                    width={46}
                    height={68}
                    className="rounded shadow-md object-cover object-top"
                    style={{ width: 46, height: 68, minWidth: 46 }}
                  />
                ) : (
                  <div
                    className="rounded border border-dashed border-[var(--border-muted)] bg-[var(--bg)]/60 flex items-center justify-center text-[7px] text-gray-500"
                    style={{ width: 46, height: 68, minWidth: 46 }}
                  >
                    No cover
                  </div>
                )}
              </div>
            </div>
            <div className="flex-1 space-y-1 min-w-0">
              <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Find Releases
              </p>
              <h3 id={titleId} className="text-lg font-semibold leading-snug truncate">
                {book.title || 'Untitled'}
              </h3>
              <p className="text-sm text-gray-600 dark:text-gray-300 truncate">
                {book.author || 'Unknown author'}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button
                type="button"
                onClick={handleClose}
                className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                aria-label="Close"
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </header>

          {/* Scrollable content */}
          <div ref={scrollContainerRef} className="flex-1 min-h-0 overflow-y-auto">
            {/* Book summary - scrolls with content */}
            {!isRequestMode && (
              <div ref={bookSummaryRef} className="flex gap-4 px-5 py-4 border-b border-[var(--border-muted)]">
                {book.preview ? (
                  <img
                    src={book.preview}
                    alt="Book cover"
                    className={`rounded-lg shadow-md object-cover object-top flex-shrink-0 ${book.series_name ? 'w-24 h-[144px]' : 'w-20 h-[120px]'}`}
                  />
                ) : (
                  <div className={`rounded-lg border border-dashed border-[var(--border-muted)] bg-[var(--bg)]/60 flex items-center justify-center text-[10px] text-gray-500 flex-shrink-0 ${book.series_name ? 'w-24 h-[144px]' : 'w-20 h-[120px]'}`}>
                    No cover
                  </div>
                )}
                <div className="flex-1 min-w-0 space-y-2">
                  {/* Metadata row */}
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-600 dark:text-gray-400">
                    {book.year && <span>{book.year}</span>}
                    {displayFields?.starField && (
                      <span className="flex items-center gap-1.5">
                        <StarRating rating={parseFloat(displayFields.starField.value || '0')} />
                        <span>{displayFields.starField.value}</span>
                        {displayFields.ratingsField && (
                          <span className="text-gray-400 dark:text-gray-500">({displayFields.ratingsField.value})</span>
                        )}
                      </span>
                    )}
                    {displayFields?.usersField && (
                      <span className="flex items-center gap-1">
                        <svg className="h-3.5 w-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
                        </svg>
                        {displayFields.usersField.value} readers
                      </span>
                    )}
                    {displayFields?.pagesField && (
                      <span>{displayFields.pagesField.value} pages</span>
                    )}
                  </div>

                  {/* Series info */}
                  {book.series_name && (
                    <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                      <span>
                        {book.series_position != null ? (
                          <>#{Number.isInteger(book.series_position) ? book.series_position : book.series_position}{book.series_count ? ` of ${book.series_count}` : ''} in {book.series_name}</>
                        ) : (
                          <>Part of {book.series_name}</>
                        )}
                      </span>
                      {onSearchSeries && (
                        <button
                          type="button"
                          onClick={() => {
                            onSearchSeries(book.series_name!);
                            handleClose();
                          }}
                          className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 rounded-full hover:bg-emerald-100 dark:hover:bg-emerald-900/40 transition-colors"
                        >
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
                          </svg>
                          View series
                        </button>
                      )}
                    </div>
                  )}

                  {/* Description */}
                  {book.description && (
                    <div className="text-sm text-gray-600 dark:text-gray-400 relative">
                      <p ref={descriptionRef} className={descriptionExpanded ? '' : 'line-clamp-3'}>
                        {book.description}
                        {descriptionExpanded && descriptionOverflows && (
                          <>
                            {' '}
                            <button
                              type="button"
                              onClick={() => setDescriptionExpanded(false)}
                              className="text-emerald-600 dark:text-emerald-400 hover:underline font-medium inline"
                            >
                              Show less
                            </button>
                          </>
                        )}
                      </p>
                      {!descriptionExpanded && descriptionOverflows && (
                        <button
                          type="button"
                          onClick={() => setDescriptionExpanded(true)}
                          className="absolute bottom-0 right-0 text-emerald-600 dark:text-emerald-400 hover:underline font-medium pl-8 bg-gradient-to-r from-transparent via-[var(--bg)] to-[var(--bg)] sm:via-[var(--bg-soft)] sm:to-[var(--bg-soft)]"
                        >
                          more
                        </button>
                      )}
                    </div>
                  )}

                  {/* Links row */}
                  <div className="flex flex-wrap items-center gap-3 text-xs">
                    {(book.isbn_13 || book.isbn_10) && (
                      <span className="text-gray-500 dark:text-gray-400">
                        ISBN: {book.isbn_13 || book.isbn_10}
                      </span>
                    )}
                    {book.source_url && (
                      <a
                        href={book.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 hover:underline"
                      >
                        View on {providerDisplay}
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                        </svg>
                      </a>
                    )}
                    {onRequestBook && (
                      <button
                        type="button"
                        onClick={() => {
                          void handleRequestBook();
                        }}
                        disabled={isRequestingBook}
                        className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 rounded-full hover:bg-emerald-100 dark:hover:bg-emerald-900/40 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                        </svg>
                        {isRequestingBook ? 'Adding...' : 'Add to requests'}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Source tabs + filters - sticky within scroll container */}
            <div className="sticky top-0 z-10 border-b border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
              {sourcesLoading ? (
                <div className="flex gap-1 px-5 py-2">
                  <div className="h-10 w-32 animate-pulse bg-gray-200 dark:bg-gray-700 rounded" />
                </div>
              ) : (
                <div className="flex items-center justify-between px-5">
                  {/* Tabs - scrollable on narrow screens */}
                  <div className="flex-1 min-w-0 overflow-x-auto scrollbar-hide">
                    <div className="relative flex gap-1">
                      {/* Sliding indicator */}
                      <div
                        className="absolute bottom-0 h-0.5 bg-emerald-600 transition-all duration-300 ease-out"
                        style={{
                          left: tabIndicatorStyle.left,
                          width: tabIndicatorStyle.width,
                        }}
                      />
                      {allTabs.map((tab) => (
                        <button
                          key={tab.name}
                          ref={(el) => { tabRefs.current[tab.name] = el; }}
                          onClick={() => setActiveTab(tab.name)}
                          className={`px-4 py-2.5 text-sm font-medium border-b-2 border-transparent transition-colors whitespace-nowrap ${activeTab === tab.name
                              ? 'text-emerald-600 dark:text-emerald-400'
                              : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                            }`}
                        >
                          {tab.displayName}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="flex items-center gap-3 pl-2 pr-1">
                    {/* Manual query button */}
                    <button
                      type="button"
                      onClick={() => {
                        setShowManualQuery((prev) => {
                          const next = !prev;
                          if (next && !manualQuery.trim()) {
                            const baseTitle = book?.search_title || book?.title || '';
                            const baseAuthor = book?.search_author || book?.author || '';
                            const defaultQuery = `${baseTitle} ${baseAuthor}`.trim();
                            setManualQuery(defaultQuery);
                          }
                          return next;
                        });
                      }}
                      className={`p-2.5 rounded-full transition-colors hover-surface text-gray-500 dark:text-gray-400 ${manualQuery.trim() ? 'text-emerald-600 dark:text-emerald-400' : ''
                        }`}
                      aria-label="Manual search query"
                      title="Manual query"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 0 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10" />
                      </svg>
                    </button>

                    {/* Sort dropdown - show if source has sort options or multiple formats */}
                    {(allSortOptions.length > 0 || availableFormats.length > 1) && (
                      <Dropdown
                        align="right"
                        widthClassName="w-auto flex-shrink-0"
                        panelClassName="w-48"
                        renderTrigger={({ isOpen, toggle }) => (
                          <button
                            type="button"
                            onClick={toggle}
                            className={`relative p-2.5 rounded-full transition-colors hover-surface text-gray-500 dark:text-gray-400 ${isOpen ? 'bg-[var(--hover-surface)]' : ''
                              }`}
                            aria-label="Sort releases"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M3 7.5 7.5 3m0 0L12 7.5M7.5 3v13.5m13.5 0L16.5 21m0 0L12 16.5m4.5 4.5V7.5" />
                            </svg>
                            {currentSort && (
                              <span className="absolute top-1 right-1 w-2 h-2 bg-emerald-500 rounded-full" />
                            )}
                          </button>
                        )}
                      >
                        {({ close }) => (
                          <div className="py-1">
                            {/* Default option - book-title best match */}
                            <button
                              type="button"
                              onClick={() => {
                                handleSortChange(null, 'asc');
                                setFormatSortExpanded(false);
                                close();
                              }}
                              className={`w-full px-3 py-2 text-left text-sm flex items-center justify-between hover-surface rounded ${!currentSort
                                  ? 'text-emerald-600 dark:text-emerald-400 font-medium'
                                  : 'text-gray-700 dark:text-gray-300'
                                }`}
                            >
                              <span>Best Match (Default)</span>
                              {!currentSort && (
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                                </svg>
                              )}
                            </button>
                            {allSortOptions.map((opt) => {
                              const isSelected = currentSort?.key === opt.sortKey;
                              const direction = isSelected ? currentSort?.direction : null;
                              return (
                                <button
                                  key={opt.sortKey}
                                  type="button"
                                  onClick={() => {
                                    handleSortChange(opt.sortKey, opt.defaultDirection);
                                    setFormatSortExpanded(false);
                                    // Don't close - allow toggling direction
                                    if (!isSelected) close();
                                  }}
                                  className={`w-full px-3 py-2 text-left text-sm flex items-center justify-between hover-surface rounded ${isSelected
                                      ? 'text-emerald-600 dark:text-emerald-400 font-medium'
                                      : 'text-gray-700 dark:text-gray-300'
                                    }`}
                                >
                                  <span>{opt.label}</span>
                                  {isSelected && direction && (
                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                                      {direction === 'asc' ? (
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
                                      ) : (
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                                      )}
                                    </svg>
                                  )}
                                </button>
                              );
                            })}

                            {/* Format priority sort sub-menu */}
                            {availableFormats.length > 1 && (
                              <>
                                {allSortOptions.length > 0 && (
                                  <div className="mx-2 my-1 border-t border-gray-200 dark:border-gray-700" />
                                )}
                                <button
                                  type="button"
                                  onClick={() => setFormatSortExpanded(prev => !prev)}
                                  className={`w-full px-3 py-2 text-left text-sm flex items-center justify-between hover-surface rounded ${
                                    currentSort?.key === FORMAT_SORT_KEY
                                      ? 'text-emerald-600 dark:text-emerald-400 font-medium'
                                      : 'text-gray-700 dark:text-gray-300'
                                  }`}
                                >
                                  <span>
                                    Format{currentSort?.key === FORMAT_SORT_KEY && currentSort.value ? ` (${currentSort.value.toUpperCase()})` : ''}
                                  </span>
                                  <svg
                                    className={`w-4 h-4 transition-transform ${formatSortExpanded ? 'rotate-90' : ''}`}
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                    strokeWidth={2}
                                  >
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                  </svg>
                                </button>
                                {formatSortExpanded && availableFormats.map((fmt) => {
                                  const isSelected = currentSort?.key === FORMAT_SORT_KEY && currentSort.value === fmt;
                                  const direction = isSelected ? currentSort?.direction : null;
                                  return (
                                    <button
                                      key={fmt}
                                      type="button"
                                      onClick={() => {
                                        handleSortChange(FORMAT_SORT_KEY, 'asc', fmt);
                                        if (!isSelected) close();
                                      }}
                                      className={`w-full pl-6 pr-3 py-1.5 text-left text-sm flex items-center justify-between hover-surface rounded ${
                                        isSelected
                                          ? 'text-emerald-600 dark:text-emerald-400 font-medium'
                                          : 'text-gray-700 dark:text-gray-300'
                                      }`}
                                    >
                                      <span>{fmt.toUpperCase()}</span>
                                      {isSelected && direction && (
                                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                                          {direction === 'asc' ? (
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
                                          ) : (
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                                          )}
                                        </svg>
                                      )}
                                    </button>
                                  );
                                })}
                              </>
                            )}
                          </div>
                        )}
                      </Dropdown>
                    )}

                    {/* Filter funnel button - stays fixed */}
                    {/* Only show filter button if source supports at least one filter type */}
                    {((columnConfig.supported_filters?.includes('format') && availableFormats.length > 0) ||
                      (columnConfig.supported_filters?.includes('language') && bookLanguages.length > 0) ||
                      (columnConfig.supported_filters?.includes('indexer') && availableIndexers.length > 1)) && (
                      <Dropdown
                        align="right"
                        widthClassName="w-auto flex-shrink-0"
                        panelClassName="w-56"
                        noScrollLimit
                        renderTrigger={({ isOpen, toggle }) => {
                          // Active filter: format is set, language is not default, or indexers differ from defaults
                          const hasLanguageFilter = !(languageFilter.length === 1 && languageFilter[0] === LANGUAGE_OPTION_DEFAULT);
                          const supportsIndexerFilter = columnConfig.supported_filters?.includes('indexer');
                          // Check if indexer filter differs from defaults (only after initialization)
                          // Don't show dot while loading or before filter is initialized from defaults
                          const hasResults = releasesBySource[activeTab]?.releases !== undefined;
                          const isInitialized = indexerFilterInitializedRef.current.has(activeTab);
                          const defaultIndexers = columnConfig.default_indexers ?? [];
                          const indexersMatchDefault = (
                            indexerFilter.length === defaultIndexers.length &&
                            indexerFilter.every((idx) => defaultIndexers.includes(idx))
                          );
                          const hasIndexerFilter = supportsIndexerFilter && hasResults && isInitialized && !indexersMatchDefault;
                          const hasActiveFilter = formatFilter !== '' || hasLanguageFilter || hasIndexerFilter;
                          return (
                            <button
                              type="button"
                              onClick={toggle}
                              className={`relative p-2.5 rounded-full transition-colors hover-surface text-gray-500 dark:text-gray-400 ${
                                isOpen ? 'bg-[var(--hover-surface)]' : ''
                              }`}
                              aria-label="Filter releases"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z" />
                              </svg>
                              {hasActiveFilter && (
                                <span className="absolute top-1 right-1 w-2 h-2 bg-emerald-500 rounded-full" />
                              )}
                            </button>
                          );
                        }}
                      >
                        {({ close }) => (
                          <div className="p-4 space-y-4">
                            {columnConfig.supported_filters?.includes('format') && availableFormats.length > 0 && (
                              <DropdownList
                                label="Format"
                                options={formatOptions}
                                value={formatFilter}
                                onChange={(val) => setFormatFilter(typeof val === 'string' ? val : val[0] ?? '')}
                                placeholder="All Formats"
                              />
                            )}
                            {columnConfig.supported_filters?.includes('language') && (
                              <LanguageMultiSelect
                                label="Language"
                                options={bookLanguages}
                                value={languageFilter}
                                onChange={setLanguageFilter}
                                defaultLanguageCodes={defaultLanguages}
                              />
                            )}
                            {columnConfig.supported_filters?.includes('indexer') && availableIndexers.length > 1 && (
                              <DropdownList
                                label="Indexers"
                                options={availableIndexers.map((idx) => ({ value: idx, label: idx }))}
                                multiple
                                value={indexerFilter}
                                onChange={(val) => setIndexerFilter(Array.isArray(val) ? val : val ? [val] : [])}
                                placeholder="All Indexers"
                              />
                            )}
                            {/* Apply button - re-fetch with server-side filters/expansion (e.g. language-aware searches) */}
                            {(activeTab === 'direct_download' || activeTab === 'prowlarr') && (
                              <button
                                type="button"
                                onClick={async () => {
                                  close();
                                  if (!book?.provider || !book?.provider_id) return;

                                  const provider = book.provider;
                                  const bookId = book.provider_id;

                                  // Clear cache and state
                                  invalidateCachedReleases(provider, bookId, activeTab, contentType);
                                  setExpandedBySource((prev) => {
                                    const next = { ...prev };
                                    delete next[activeTab];
                                    return next;
                                  });
                                  setErrorBySource((prev) => {
                                    const next = { ...prev };
                                    delete next[activeTab];
                                    return next;
                                  });

                                  // Fetch with language filter
                                  setLoadingBySource((prev) => ({ ...prev, [activeTab]: true }));
                                  try {
                                    // Resolve language codes for the API call
                                    const languagesParam = getReleaseSearchLanguageParams(languageFilter, bookLanguages, defaultLanguages);

                                    // Pass indexer filter only if the source supports it (empty array = search all)
                                    const supportsIndexerFilter = columnConfig.supported_filters?.includes('indexer');
                                    const indexersParam = supportsIndexerFilter && indexerFilter.length > 0 ? indexerFilter : undefined;

                                    const response = await getReleases(
                                      provider, bookId, activeTab, book.title, book.author, false, languagesParam, contentType, manualQuery.trim() || undefined, indexersParam
                                    );
                                    setCachedReleases(provider, bookId, activeTab, contentType, response);
                                    setReleasesBySource((prev) => ({ ...prev, [activeTab]: response }));
                                  } catch (err) {
                                    const message = err instanceof Error ? err.message : 'Failed to fetch releases';
                                    setErrorBySource((prev) => ({ ...prev, [activeTab]: message }));
                                  } finally {
                                    setLoadingBySource((prev) => ({ ...prev, [activeTab]: false }));
                                  }
                                }}
                                className="w-full px-3 py-2 text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg transition-colors"
                              >
                                Apply
                              </button>
                            )}
                          </div>
                        )}
                      </Dropdown>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Manual query panel (below source tabs) */}
            {showManualQuery && (
              <div className="px-5 py-3 border-b border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
                <form
                  className="flex items-center gap-2"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    if (!book?.provider || !book?.provider_id) return;

                    const q = manualQuery.trim();
                    if (!q) return;

                    const provider = book.provider;
                    const bookId = book.provider_id;

                    // Clear cache + clear visible results so user gets feedback.
                    invalidateCachedReleases(provider, bookId, activeTab, contentType);
                    setExpandedBySource((prev) => {
                      const next = { ...prev };
                      delete next[activeTab];
                      return next;
                    });
                    setErrorBySource((prev) => ({ ...prev, [activeTab]: null }));
                    setReleasesBySource((prev) => ({ ...prev, [activeTab]: null }));

                    setLoadingBySource((prev) => ({ ...prev, [activeTab]: true }));
                    try {
                      const response = await getReleases(
                        provider,
                        bookId,
                        activeTab,
                        book.title,
                        book.author,
                        false,
                        undefined,
                        contentType,
                        q
                      );
                      setCachedReleases(provider, bookId, activeTab, contentType, response);
                      setReleasesBySource((prev) => ({ ...prev, [activeTab]: response }));
                    } catch (err) {
                      const message = err instanceof Error ? err.message : 'Failed to fetch releases';
                      setErrorBySource((prev) => ({ ...prev, [activeTab]: message }));
                    } finally {
                      setLoadingBySource((prev) => ({ ...prev, [activeTab]: false }));
                    }
                  }}
                >
                  <input
                    type="text"
                    value={manualQuery}
                    onChange={(e) => setManualQuery(e.target.value)}
                    placeholder="Type a custom search query (overrides all sources)"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] text-[var(--text)]"
                  />
                  <button
                    type="submit"
                    disabled={currentTabLoading || !manualQuery.trim()}
                    className={`px-3 py-2 text-sm font-medium text-white rounded-lg transition-colors ${currentTabLoading || !manualQuery.trim()
                        ? 'bg-emerald-600/60 cursor-not-allowed'
                        : 'bg-emerald-600 hover:bg-emerald-700'
                      }`}
                  >
                    {currentTabLoading ? 'Searching…' : 'Search'}
                  </button>
                </form>
                <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                  Manual query overrides ISBN/title/author/language expansion.
                </p>
              </div>
            )}

            {/* Release list content */}
            <div className="min-h-[200px]">
              {sourcesLoading ? (
                <ReleaseSkeleton />
              ) : isInitialLoading && filteredReleases.length === 0 ? (
                <ReleaseSkeleton />
              ) : currentTabError ? (
                <ErrorState message={currentTabError} />
              ) : filteredReleases.length === 0 && !currentTabLoading ? (
                <>
                  <EmptyState
                    message={
                      formatFilter
                        ? `No ${formatFilter.toUpperCase()} releases found. Try a different format.`
                        : 'No releases found for this book.'
                    }
                  />
                  {/* Action button - plugin-defined or default expand search */}
                  {(columnConfig.action_button || (
                    !expandedBySource[activeTab] &&
                    releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type &&
                    !['title_author', 'expanded'].includes(
                      releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type ?? ''
                    )
                  )) && (
                      <div className="py-3 text-center">
                        <button
                          type="button"
                          onClick={handleExpandSearch}
                          className="px-3 py-1.5 text-sm text-gray-500 dark:text-gray-400 rounded-full hover-action transition-all duration-200"
                        >
                          {columnConfig.action_button?.label ?? 'Expand search'}
                        </button>
                      </div>
                    )}
                </>
              ) : (
                <>
                  {/* Key includes filter to force remount when filter changes */}
                  <div key={`releases-${formatFilter}-${languageFilter.join(',')}`} className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                    {filteredReleases.map((release, index) => (
                      <ReleaseRow
                        key={`${release.source}-${release.source_id}`}
                        release={release}
                        index={index}
                        onDownload={() => handleReleaseAction(release)}
                        buttonState={getButtonState(release)}
                        columns={columnConfig.columns}
                        gridTemplate={columnConfig.grid_template}
                        leadingCell={columnConfig.leading_cell}
                        onlineServers={columnConfig.online_servers}
                      />
                    ))}
                  </div>
                  {/* Action button - plugin-defined or default expand search */}
                  {!currentTabLoading && (columnConfig.action_button || (
                    !expandedBySource[activeTab] &&
                    releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type &&
                    !['title_author', 'expanded'].includes(
                      releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type ?? ''
                    )
                  )) && (
                      <div
                        className="py-3 text-center animate-pop-up will-change-transform"
                        style={{
                          animationDelay: `${filteredReleases.length * 30}ms`,
                          animationFillMode: 'both',
                        }}
                      >
                        <button
                          type="button"
                          onClick={handleExpandSearch}
                          className="px-3 py-1.5 text-sm text-gray-500 dark:text-gray-400 rounded-full hover-action transition-all duration-200"
                        >
                          {columnConfig.action_button?.label ?? 'Expand search'}
                        </button>
                      </div>
                    )}
                  {/* Expanding search - show skeleton below existing results */}
                  {currentTabLoading && filteredReleases.length > 0 && (
                    <ReleaseSkeleton />
                  )}
                </>
              )}
            </div>

            {/* Sticky search status indicator - stays at bottom of visible scroll area */}
            {searchStatus && searchStatus.source === activeTab && currentTabLoading && (
              <div className="sticky bottom-0 z-10 flex items-center justify-center pointer-events-none pb-4 pt-2">
                <div className="flex items-center gap-2.5 px-4 py-2 rounded-xl bg-[var(--bg-soft)] border border-[var(--border-muted)] text-gray-500 dark:text-gray-400 text-sm shadow-lg pointer-events-auto">
                  {searchStatus.phase !== 'complete' && searchStatus.phase !== 'error' && (
                    <div className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
                  )}
                  {searchStatus.message}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );

  if (typeof document === 'undefined') {
    return modal;
  }

  return createPortal(modal, document.body);
};
