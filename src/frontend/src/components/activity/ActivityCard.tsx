import { ReactNode, useEffect, useRef, useState } from 'react';
import { RequestRecord } from '../../types';
import { withBasePath } from '../../utils/basePath';
import { Tooltip } from '../shared/Tooltip';
import { ActivityItem } from './activityTypes';
import {
  STATUS_BADGE_STYLES,
  STATUS_TOOLTIP_CLASSES,
  isActiveDownloadStatus,
  getProgressConfig,
} from './activityStyles';

interface ActivityCardProps {
  item: ActivityItem;
  isAdmin: boolean;
  onDownloadCancel?: (bookId: string) => void;
  onRequestCancel?: (requestId: number) => void;
  onRequestApprove?: (requestId: number, record: RequestRecord) => void;
  onRequestReject?: (requestId: number) => void;
}

const BookFallback = () => (
  <div className="w-12 h-[4.5rem] rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-[8px] font-medium text-gray-500 dark:text-gray-400">
    No Cover
  </div>
);

const IconButton = ({
  title,
  className,
  onClick,
  children,
}: {
  title: string;
  className: string;
  onClick: () => void;
  children: ReactNode;
}) => (
  <button
    type="button"
    onClick={onClick}
    title={title}
    aria-label={title}
    className={`h-7 w-7 rounded-full inline-flex items-center justify-center transition-colors ${className}`}
  >
    {children}
  </button>
);

const getBadgeText = (item: ActivityItem): string => {
  if (item.statusDetail) {
    return item.statusDetail;
  }
  if (item.visualStatus === 'downloading' && typeof item.progress === 'number') {
    return `Downloading ${Math.round(item.progress)}%`;
  }
  return item.statusLabel;
};

const buildRequestNoteLine = (item: ActivityItem): string | null => {
  if (item.requestNote && item.visualStatus === 'pending') {
    return `"${item.requestNote}"`;
  }
  if (item.adminNote && (item.visualStatus === 'rejected' || item.visualStatus === 'fulfilled')) {
    return `"${item.adminNote}"`;
  }
  return null;
};

export const ActivityCard = ({
  item,
  isAdmin,
  onDownloadCancel,
  onRequestCancel,
  onRequestApprove,
  onRequestReject,
}: ActivityCardProps) => {
  const badgeRef = useRef<HTMLSpanElement>(null);
  const [badgeOverflowing, setBadgeOverflowing] = useState(false);
  const badgeStyle = STATUS_BADGE_STYLES[item.visualStatus];
  const isActiveDownload = item.kind === 'download' && isActiveDownloadStatus(item.visualStatus);
  const progressConfig = isActiveDownload ? getProgressConfig(item.visualStatus, item.progress) : null;
  const noteLine = buildRequestNoteLine(item);
  const badgeText = getBadgeText(item);

  useEffect(() => {
    const el = badgeRef.current;
    if (el) {
      setBadgeOverflowing(el.scrollWidth > el.clientWidth);
    }
  }, [badgeText]);

  const renderActions = () => {
    if (item.kind === 'download' && item.downloadBookId && onDownloadCancel) {
      if (item.visualStatus === 'queued') {
        return (
          <IconButton
            title="Remove from queue"
            className="text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30"
            onClick={() => onDownloadCancel(item.downloadBookId!)}
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </IconButton>
        );
      }

      if (item.visualStatus === 'resolving' || item.visualStatus === 'locating' || item.visualStatus === 'downloading') {
        return (
          <IconButton
            title="Stop download"
            className="text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30"
            onClick={() => onDownloadCancel(item.downloadBookId!)}
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </IconButton>
        );
      }

      return (
        <IconButton
          title="Dismiss"
          className="text-gray-500 hover:text-red-600 hover:bg-red-100 dark:hover:bg-red-900/30"
          onClick={() => onDownloadCancel(item.downloadBookId!)}
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </IconButton>
      );
    }

    if (item.kind === 'request' && item.requestId && item.visualStatus === 'pending') {
      if (isAdmin) {
        return (
          <>
            {onRequestApprove && item.requestRecord && (
              <IconButton
                title="Approve request"
                className="text-green-600 dark:text-green-400 hover:bg-green-100 dark:hover:bg-green-900/30"
                onClick={() => onRequestApprove(item.requestId!, item.requestRecord!)}
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />
                </svg>
              </IconButton>
            )}
            {onRequestReject && (
              <IconButton
                title="Reject request"
                className="text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30"
                onClick={() => onRequestReject(item.requestId!)}
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </IconButton>
            )}
          </>
        );
      }

      if (onRequestCancel) {
        return (
          <IconButton
            title="Cancel request"
            className="text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30"
            onClick={() => onRequestCancel(item.requestId!)}
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </IconButton>
        );
      }
    }

    return null;
  };

  const titleNode =
    item.kind === 'download' &&
    item.visualStatus === 'complete' &&
    item.downloadPath &&
    item.downloadBookId ? (
      <a
        href={withBasePath(`/api/localdownload?id=${encodeURIComponent(item.downloadBookId)}`)}
        className="text-sky-600 hover:underline"
      >
        {item.title}
      </a>
    ) : (
      item.title
    );

  return (
    <div>
      <div className="flex gap-3 items-start">
        {/* Artwork */}
        <div className="w-12 h-[4.5rem] rounded flex-shrink-0 overflow-hidden bg-gray-200 dark:bg-gray-700">
          {item.preview ? (
            <img
              src={item.preview}
              alt={`${item.title} cover`}
              className="w-full h-full object-cover object-top"
            />
          ) : (
            <BookFallback />
          )}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0 py-0.5">
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm truncate leading-tight min-w-0" title={`${item.title} — ${item.author}`}>
              <span className="font-semibold">{titleNode}</span>
              {item.author && <span className="opacity-60 text-xs"> — {item.author}</span>}
            </p>
            <div className="flex-shrink-0 inline-flex items-center gap-1 -my-1">{renderActions()}</div>
          </div>

          <p className="text-xs opacity-50 truncate mt-0.5" title={item.metaLine}>
            {item.metaLine}
          </p>

          {noteLine && (
            <p className="text-[11px] opacity-60 italic truncate mt-0.5" title={noteLine}>
              {noteLine}
            </p>
          )}

          <div className="mt-1.5">
            <Tooltip
              content={badgeOverflowing ? badgeText : undefined}
              delay={0}
              position="bottom"
              unstyled
              className={STATUS_TOOLTIP_CLASSES[item.visualStatus]}
            >
              <span
                ref={badgeRef}
                className={`relative inline-block max-w-full px-2 py-0.5 rounded-md text-[11px] font-medium truncate ${badgeStyle.bg} ${badgeStyle.text}`}
              >
                {progressConfig && badgeStyle.fillColor && (
                  <span
                    className="absolute inset-y-0 left-0 rounded-md transition-[width] duration-300"
                    style={{
                      width: `${progressConfig.percent}%`,
                      backgroundColor: badgeStyle.fillColor,
                    }}
                  />
                )}
                {progressConfig && badgeStyle.waveColor && (
                  <span
                    className="absolute inset-y-0 left-0 rounded-md activity-wave"
                    style={{
                      width: `${progressConfig.percent}%`,
                      background: `linear-gradient(90deg, transparent 0%, ${badgeStyle.waveColor} 50%, transparent 100%)`,
                      backgroundSize: '200% 100%',
                    }}
                  />
                )}
                <span className="relative">{badgeText}</span>
              </span>
            </Tooltip>
          </div>
        </div>
      </div>

    </div>
  );
};
