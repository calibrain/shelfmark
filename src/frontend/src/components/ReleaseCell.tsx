import { ColumnSchema, Release } from '../types';
import { getColorStyleFromHint, getProtocolDotColor, getFormatColor } from '../utils/colorMaps';
import { getNestedValue } from '../utils/objectHelpers';
import { Tooltip } from './shared/Tooltip';

interface ReleaseCellProps {
  column: ColumnSchema;
  release: Release;
  compact?: boolean;  // When true, renders badges as plain text (for mobile info lines)
  onlineServers?: string[];  // For IRC: list of online server nicks to show status indicator
}

/**
 * Generic cell renderer for release list columns.
 * Renders different column types (text, badge, size, number, seeders) based on schema.
 * When compact=true, badges render as plain text for use in mobile info lines.
 */
export const ReleaseCell = ({ column, release, compact = false, onlineServers }: ReleaseCellProps) => {
  const rawValue = getNestedValue(release as unknown as Record<string, unknown>, column.key);
  const value = rawValue !== undefined && rawValue !== null
    ? String(rawValue)
    : column.fallback;

  const displayValue = column.uppercase ? value.toUpperCase() : value;

  // Alignment classes
  const alignClass = {
    left: 'text-left justify-start',
    center: 'text-center justify-center',
    right: 'text-right justify-end',
  }[column.align];

  // Render based on type
  switch (column.render_type) {
    case 'badge': {
      // Compact mode: render as plain text (for mobile info lines)
      if (compact) {
        return <span>{displayValue}</span>;
      }
      const colorStyle = getColorStyleFromHint(value, column.color_hint);

      // Build rich tooltip content for formats
      let tooltipContent: React.ReactNode = null;
      if (column.key === 'extra.formats_display') {
        const formats = (release.extra as Record<string, unknown> | undefined)?.formats;
        if (Array.isArray(formats) && formats.length > 1) {
          tooltipContent = (
            <div className="flex flex-wrap gap-1.5">
              {formats.map((fmt) => {
                const fmtColor = getFormatColor(String(fmt));
                return (
                  <span
                    key={String(fmt)}
                    className={`${fmtColor.bg} ${fmtColor.text} text-[10px] sm:text-[11px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-lg tracking-wide`}
                  >
                    {String(fmt).toUpperCase()}
                  </span>
                );
              })}
            </div>
          );
        }
      }

      const badge = (
        <span
          className={`${colorStyle.bg} ${colorStyle.text} text-[10px] sm:text-[11px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-lg tracking-wide`}
        >
          {displayValue}
        </span>
      );

      return (
        <div className={`flex items-center ${alignClass}`}>
          {value !== column.fallback ? (
            tooltipContent ? (
              <Tooltip content={tooltipContent} position="top">
                {badge}
              </Tooltip>
            ) : (
              badge
            )
          ) : (
            <span className="text-[10px] sm:text-xs text-gray-500 dark:text-gray-400">{column.fallback}</span>
          )}
        </div>
      );
    }

    case 'tags': {
      const tags = Array.isArray(rawValue)
        ? rawValue.map((tag) => String(tag)).filter((tag) => tag.trim())
        : rawValue !== undefined && rawValue !== null && String(rawValue).trim()
          ? [String(rawValue)]
          : [];
      const isFlags = column.color_hint?.type === 'map' && column.color_hint.value === 'flags';

      const normalizeFlagLabel = (tag: string): string => {
        const normalized = tag.trim().toLowerCase();
        if (!normalized) return tag;
        switch (normalized) {
          case 'freeleech':
          case 'free leech':
          case 'fl':
            return 'FL';
          case 'double upload':
          case 'doubleupload':
          case 'du':
            return 'DU';
          case 'vip':
            return 'VIP';
          case 'internal':
          case 'int':
            return 'INT';
          default:
            return tag;
        }
      };

      if (compact) {
        if (tags.length === 0) {
          return column.fallback ? <span>{column.fallback}</span> : null;
        }
        const displayTags = tags.map((tag) => {
          const normalized = isFlags ? normalizeFlagLabel(tag) : tag;
          return column.uppercase ? normalized.toUpperCase() : normalized;
        });
        return <span>{displayTags.join(', ')}</span>;
      }

      if (!tags.length) {
        if (!column.fallback) {
          return <div className={`flex items-center ${alignClass}`} />;
        }
        return (
          <div className={`flex items-center ${alignClass}`}>
            <span className="text-[10px] sm:text-xs text-gray-500 dark:text-gray-400">{column.fallback}</span>
          </div>
        );
      }

      return (
        <div className={`flex flex-wrap items-center gap-1.5 ${alignClass}`}>
          {tags.map((tag, idx) => {
            const normalized = isFlags ? normalizeFlagLabel(tag) : tag;
            const displayTag = column.uppercase ? normalized.toUpperCase() : normalized;
            const colorStyle = getColorStyleFromHint(tag, column.color_hint);

            return (
              <span
                key={`${tag}-${idx}`}
                className={`${colorStyle.bg} ${colorStyle.text} text-[10px] sm:text-[11px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-lg tracking-wide whitespace-nowrap`}
              >
                {displayTag}
              </span>
            );
          })}
        </div>
      );
    }

    case 'size': {
      // Build tooltip from extra metadata (torznab attrs, publish date, etc.)
      const extra = release.extra as Record<string, unknown> | undefined;
      const torznabAttrs = extra?.torznab_attrs as Record<string, string> | undefined;
      const publishDate = extra?.publish_date as string | undefined;

      // Helper to format relative time
      const formatRelativeTime = (dateStr: string): string | null => {
        try {
          const date = new Date(dateStr);
          if (isNaN(date.getTime())) return null;
          const now = new Date();
          const diffMs = now.getTime() - date.getTime();
          const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
          if (diffDays === 0) return 'Today';
          if (diffDays === 1) return '1 day ago';
          if (diffDays < 30) return `${diffDays} days ago`;
          const diffMonths = Math.floor(diffDays / 30);
          if (diffMonths === 1) return '1 month ago';
          if (diffMonths < 12) return `${diffMonths} months ago`;
          const diffYears = Math.floor(diffDays / 365);
          if (diffYears === 1) return '1 year ago';
          return `${diffYears} years ago`;
        } catch {
          return null;
        }
      };

      const rows: Array<{ label: string; value: string }> = [];

      // Add publish date first if available
      if (publishDate) {
        const relativeTime = formatRelativeTime(publishDate);
        if (relativeTime) {
          rows.push({ label: 'Added', value: relativeTime });
        }
      }

      // Add torznab attributes if available (MAM, etc.)
      if (torznabAttrs && Object.keys(torznabAttrs).length > 0) {
        const displayAttrs: Array<{ key: string; label: string }> = [
          { key: 'description', label: 'Description' },
          { key: 'year', label: 'Year' },
          { key: 'genre', label: 'Genre' },
          { key: 'narrator', label: 'Narrator' },
          { key: 'bitrate', label: 'Bitrate' },
          { key: 'samplerate', label: 'Sample Rate' },
          { key: 'runtime', label: 'Runtime' },
          { key: 'pages', label: 'Pages' },
          { key: 'publisher', label: 'Publisher' },
          { key: 'language', label: 'Language' },
        ];

        for (const attr of displayAttrs) {
          const val = torznabAttrs[attr.key];
          if (val && val.trim()) {
            rows.push({ label: attr.label, value: val.trim() });
          }
        }
      }

      // Add files and grabs from extra
      const files = extra?.files as number | undefined;
      const grabs = extra?.grabs as number | undefined;
      if (files !== undefined && files !== null) {
        rows.push({ label: 'Files', value: String(files) });
      }
      if (grabs !== undefined && grabs !== null) {
        rows.push({ label: 'Grabs', value: String(grabs) });
      }

      let sizeTooltipContent: React.ReactNode = null;
      if (rows.length > 0) {
        sizeTooltipContent = (
          <div className="flex flex-col gap-1 max-w-xs">
            {rows.map((row) => (
              <div key={row.label} className="flex gap-2">
                <span className="text-gray-400 dark:text-gray-500 flex-shrink-0">{row.label}:</span>
                <span className="truncate">{row.value}</span>
              </div>
            ))}
          </div>
        );
      }

      if (compact) {
        return <span>{displayValue}</span>;
      }

      const sizeText = <span>{displayValue}</span>;

      return (
        <div className={`flex items-center ${alignClass} text-xs text-gray-600 dark:text-gray-300`}>
          {sizeTooltipContent ? (
            <Tooltip content={sizeTooltipContent} position="left">
              {sizeText}
            </Tooltip>
          ) : (
            sizeText
          )}
        </div>
      );
    }

    case 'peers': {
      // Peers display: "S/L" string with badge colored by seeder count
      // Color logic: 0 = red, 1-10 = yellow, 10+ = blue
      const seeders = release.seeders;
      const peersValue = value || column.fallback;
      const isFallback = seeders == null || peersValue === column.fallback;

      // If no data, show plain text like badge type does
      if (isFallback) {
        if (compact) {
          return <span>{column.fallback}</span>;
        }
        return (
          <div className={`flex items-center ${alignClass}`}>
            <span className="text-[10px] sm:text-xs text-gray-500 dark:text-gray-400">{column.fallback}</span>
          </div>
        );
      }

      // Determine color based on seeder count
      let badgeColors: string;
      if (seeders >= 10) {
        badgeColors = 'bg-blue-500/20 text-blue-700 dark:text-blue-300';
      } else if (seeders >= 1) {
        badgeColors = 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-300';
      } else {
        badgeColors = 'bg-red-500/20 text-red-700 dark:text-red-300';
      }

      if (compact) {
        return <span className={`font-medium ${badgeColors.split(' ').slice(1).join(' ')}`}>{peersValue}</span>;
      }
      return (
        <div className={`flex items-center ${alignClass}`}>
          <span className={`${badgeColors} text-[10px] sm:text-[11px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-lg tracking-wide`}>
            {peersValue}
          </span>
        </div>
      );
    }

    case 'indexer_protocol': {
      // Indexer name with colored dot indicating protocol (torrent/usenet) and peers count
      const protocol = release.protocol as string | undefined;
      const dotColor = getProtocolDotColor(protocol);
      const protocolLabel = protocol === 'torrent' ? 'Torrent' : protocol === 'nzb' ? 'Usenet' : protocol || 'Unknown';
      const peers = release.peers;

      if (compact) {
        return (
          <span className="inline-flex items-center gap-1">
            <span
              className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor}`}
              title={protocolLabel}
            />
            {displayValue}
            {peers && <span className="text-gray-400">({peers})</span>}
          </span>
        );
      }
      return (
        <div className={`flex items-center ${alignClass} text-xs text-gray-600 dark:text-gray-300 truncate gap-1.5`}>
          <span
            className={`w-2 h-2 rounded-full flex-shrink-0 ${dotColor}`}
            title={protocolLabel}
          />
          <span className="truncate">{displayValue}</span>
          {peers && <span className="text-gray-400 dark:text-gray-500 flex-shrink-0">{peers}</span>}
        </div>
      );
    }

    case 'flag_icon': {
      // Colored badge showing FL, VIP, or both
      if (!value || value === column.fallback) {
        if (compact) return null;
        return <div className={`flex items-center ${alignClass}`} />;
      }

      const flagColor = getColorStyleFromHint(value, { type: 'map', value: 'flags' });

      if (compact) {
        return <span className={`${flagColor.text} font-medium`}>{value}</span>;
      }
      return (
        <div className={`flex items-center ${alignClass}`}>
          <span className={`${flagColor.bg} ${flagColor.text} text-[10px] sm:text-[11px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-lg tracking-wide whitespace-nowrap`}>
            {value}
          </span>
        </div>
      );
    }

    case 'format_content_type': {
      // Content type icon + format badge combined
      // Shows primary format as badge with colored dots for additional formats
      const contentType = release.content_type as string | undefined;
      const isAudiobook = contentType === 'audiobook';
      const formats = (release.extra as Record<string, unknown> | undefined)?.formats as string[] | undefined;
      const primaryFormat = formats?.[0] || null;
      const additionalFormats = formats?.slice(1) || [];

      // Use blue for book, violet for audiobook when no format specified
      const noFormatStyle = isAudiobook
        ? { bg: 'bg-violet-500/20', text: 'text-violet-600 dark:text-violet-400' }
        : { bg: 'bg-blue-500/20', text: 'text-blue-600 dark:text-blue-400' };
      const colorStyle = primaryFormat ? getFormatColor(primaryFormat) : noFormatStyle;

      // Build rich tooltip content for formats (if multiple)
      let tooltipContent: React.ReactNode = null;
      if (formats && formats.length > 1) {
        tooltipContent = (
          <div className="flex flex-wrap gap-1.5">
            {formats.map((fmt) => {
              const fmtColor = getFormatColor(String(fmt));
              return (
                <span
                  key={String(fmt)}
                  className={`${fmtColor.bg} ${fmtColor.text} text-[10px] sm:text-[11px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-lg tracking-wide`}
                >
                  {String(fmt).toUpperCase()}
                </span>
              );
            })}
          </div>
        );
      }

      // Icon sized to match visual height of format text badges
      const icon = isAudiobook ? (
        // Headphones icon for audiobook
        <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
        </svg>
      ) : (
        // Book icon for ebook
        <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
        </svg>
      );

      if (compact) {
        if (!primaryFormat) {
          return <span className="inline-flex items-center text-gray-500" title={isAudiobook ? 'Audiobook' : 'Book'}>{icon}</span>;
        }
        // Simple text tooltip for compact mode
        const compactTooltip = formats && formats.length > 1
          ? formats.map((fmt) => String(fmt).toUpperCase()).join(', ')
          : undefined;
        return (
          <span className={column.uppercase ? 'uppercase' : ''} title={compactTooltip}>
            {primaryFormat.toUpperCase()}
            {additionalFormats.length > 0 && ` +${additionalFormats.length}`}
          </span>
        );
      }

      // No format - just show icon with same width as format badges
      if (!primaryFormat) {
        return (
          <div className="flex items-center justify-start" title={isAudiobook ? 'Audiobook' : 'Book'}>
            <span className={`${colorStyle.bg} ${colorStyle.text} text-[10px] sm:text-[11px] font-semibold py-0.5 rounded-lg inline-flex items-center justify-center w-[3.25rem]`}>
              {icon}
            </span>
          </div>
        );
      }

      // Format badge - left-aligned so primary format stays in place, +N appears to right
      const formatBadge = (
        <span className="inline-flex items-center gap-1">
          <span
            className={`${colorStyle.bg} ${colorStyle.text} text-[10px] sm:text-[11px] font-semibold py-0.5 rounded-lg tracking-wide whitespace-nowrap w-[3.25rem] text-center`}
          >
            {column.uppercase ? primaryFormat.toUpperCase() : primaryFormat}
          </span>
          {additionalFormats.length > 0 && (
            <span
              className="bg-gray-500/20 text-gray-700 dark:text-gray-300 text-[10px] sm:text-[11px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-lg tracking-wide"
            >
              +{additionalFormats.length}
            </span>
          )}
        </span>
      );

      return (
        <div className="flex items-center justify-start">
          {tooltipContent ? (
            <Tooltip content={tooltipContent} position="top">
              {formatBadge}
            </Tooltip>
          ) : (
            formatBadge
          )}
        </div>
      );
    }

    case 'number':
      if (compact) {
        return <span>{displayValue}</span>;
      }
      return (
        <div className={`flex items-center ${alignClass} text-xs text-gray-600 dark:text-gray-300`}>
          {displayValue}
        </div>
      );

    case 'text':
    default: {
      // Check if this is a server column with online status data
      const isServerColumn = column.key === 'extra.server' && onlineServers !== undefined;
      const isOnline = isServerColumn && onlineServers?.includes(value);

      if (compact) {
        if (isServerColumn) {
          return (
            <span className="inline-flex items-center gap-1">
              <span
                className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isOnline ? 'bg-emerald-500' : 'bg-gray-400'}`}
                title={isOnline ? 'Online' : 'Offline'}
              />
              {displayValue}
            </span>
          );
        }
        return <span>{displayValue}</span>;
      }

      return (
        <div className={`flex items-center ${alignClass} text-xs text-gray-600 dark:text-gray-300 truncate`}>
          {isServerColumn && (
            <span
              className={`w-2 h-2 rounded-full mr-1.5 flex-shrink-0 ${isOnline ? 'bg-emerald-500' : 'bg-gray-400'}`}
              title={isOnline ? 'Online' : 'Offline'}
            />
          )}
          {displayValue}
        </div>
      );
    }
  }
};

export default ReleaseCell;
