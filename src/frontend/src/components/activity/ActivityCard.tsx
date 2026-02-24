import { ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { RequestRecord } from '../../types';
import { withBasePath } from '../../utils/basePath';
import { Tooltip } from '../shared/Tooltip';
import { ActivityItem } from './activityTypes';
import { ActivityCardAction, buildActivityCardModel } from './activityCardModel';
import {
  STATUS_BADGE_STYLES,
  STATUS_TOOLTIP_CLASSES,
  getProgressConfig,
} from './activityStyles';

interface RequestApproveOptions {
  browseOnly?: boolean;
  manualApproval?: boolean;
}

type RequestApproveHandler = (
  requestId: number,
  record: RequestRecord,
  options?: RequestApproveOptions
) => Promise<void> | void;

interface ActivityCardProps {
  item: ActivityItem;
  isAdmin: boolean;
  onDownloadCancel?: (bookId: string) => void;
  onDownloadDismiss?: (bookId: string, linkedRequestId?: number) => void;
  onRequestCancel?: (requestId: number) => void;
  onRequestApprove?: RequestApproveHandler;
  onRequestReviewApprove?: RequestApproveHandler;
  onRequestReject?: (requestId: number, adminNote?: string) => Promise<void> | void;
  onRequestRejectConfirm?: (requestId: number, adminNote?: string) => Promise<void> | void;
  onRequestDismiss?: (requestId: number) => void;
  showRequestDetailsToggle?: boolean;
  isRequestDetailsOpen?: boolean;
  onRequestDetailsToggle?: () => void;
  onRequestDetailsOpen?: () => void;
  isRequestRejectOpen?: boolean;
  onRequestRejectClose?: () => void;
  isSelected?: boolean;
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
    aria-label={title}
    className={`h-7 w-7 rounded-full inline-flex items-center justify-center transition-colors ${className}`}
  >
    {children}
  </button>
);

const actionKey = (action: ActivityCardAction): string => {
  switch (action.kind) {
    case 'download-remove':
    case 'download-stop':
    case 'download-dismiss':
      return `${action.kind}-${action.bookId}`;
    case 'request-approve':
      return `${action.kind}-${action.requestId}-${action.record.id}`;
    case 'request-reject':
    case 'request-cancel':
    case 'request-dismiss':
      return `${action.kind}-${action.requestId}`;
    default:
      return 'action';
  }
};

const actionUiConfig = (
  action: ActivityCardAction
): { title: string; className: string; icon: 'cross' | 'check' | 'stop' | 'retry' } => {
  switch (action.kind) {
    case 'download-remove':
      return {
        title: 'Remove from queue',
        className: 'text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    case 'download-stop':
      return {
        title: 'Stop download',
        className: 'text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'stop',
      };
    case 'download-dismiss':
      return {
        title: 'Clear',
        className: 'text-gray-500 hover:text-red-600 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    case 'request-approve':
      return {
        title: 'Approve',
        className: 'text-green-600 dark:text-green-400 hover:bg-green-100 dark:hover:bg-green-900/30',
        icon: 'check',
      };
    case 'request-reject':
      return {
        title: 'Reject',
        className: 'text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    case 'request-cancel':
      return {
        title: 'Cancel request',
        className: 'text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    case 'request-dismiss':
      return {
        title: 'Clear',
        className: 'text-gray-500 hover:text-red-600 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    default:
      return {
        title: 'Action',
        className: 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700',
        icon: 'cross',
      };
  }
};

const ActionIcon = ({ icon }: { icon: 'cross' | 'check' | 'stop' | 'retry' }) => {
  if (icon === 'stop') {
    return (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <rect x="6" y="6" width="12" height="12" rx="2" />
      </svg>
    );
  }
  if (icon === 'check') {
    return (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />
      </svg>
    );
  }
  if (icon === 'retry') {
    return (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M18.363 5.634A8.997 9.002 29.494 0 0 7.5 4.206 8.997 9.002 29.494 0 0 3.306 14.33 8.997 9.002 29.494 0 0 11.996 21a8.997 9.002 29.494 0 0 8.694-6.673m-2.327-8.693L20.87 8.14m.017-4.994v5.015m0 0h-5.013" />
      </svg>
    );
  }
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
};

const asRecord = (value: unknown): Record<string, unknown> => {
  if (value && typeof value === 'object') {
    return value as Record<string, unknown>;
  }
  return {};
};

const toOptionalText = (value: unknown): string | undefined => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return undefined;
};

const toSourceLabel = (value: unknown): string => {
  const text = toOptionalText(value);
  if (!text) {
    return 'Any Source';
  }
  const normalized = text.trim().toLowerCase();
  if (normalized === '*' || normalized === 'any' || normalized === 'all') {
    return 'Any Source';
  }
  return text
    .split(/[_\s-]+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

const formatDateTime = (isoDate: string): string => {
  const parsed = Date.parse(isoDate);
  if (!Number.isFinite(parsed)) {
    return isoDate;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed);
};

const hasAttachedReleaseData = (record: RequestRecord): boolean => {
  if (record.request_level !== 'release') {
    return false;
  }
  if (!record.release_data || typeof record.release_data !== 'object') {
    return false;
  }
  return Object.keys(record.release_data).length > 0;
};

const DetailField = ({ label, value }: { label: string; value: string }) => (
  <div className="py-1">
    <p className="text-[10px] uppercase tracking-wide opacity-60">{label}</p>
    <p className="text-xs font-medium break-words mt-0.5">{value}</p>
  </div>
);

const MAX_ADMIN_NOTE_LENGTH = 1000;

export const ActivityCard = ({
  item,
  isAdmin,
  onDownloadCancel,
  onDownloadDismiss,
  onRequestCancel,
  onRequestApprove,
  onRequestReviewApprove,
  onRequestReject,
  onRequestRejectConfirm,
  onRequestDismiss,
  showRequestDetailsToggle = false,
  isRequestDetailsOpen = false,
  onRequestDetailsToggle,
  onRequestDetailsOpen,
  isRequestRejectOpen = false,
  onRequestRejectClose,
  isSelected = false,
}: ActivityCardProps) => {
  const model = useMemo(() => buildActivityCardModel(item, isAdmin), [item, isAdmin]);
  const noteLine = model.noteLine;
  const badgeRefs = useRef<Record<string, HTMLSpanElement | null>>({});
  const titleLineRef = useRef<HTMLParagraphElement | null>(null);
  const [badgeOverflow, setBadgeOverflow] = useState<Record<string, boolean>>({});
  const [titleOverflow, setTitleOverflow] = useState(false);
  const [isReviewSubmitting, setIsReviewSubmitting] = useState(false);
  const [rejectNote, setRejectNote] = useState('');
  const [isRejectSubmitting, setIsRejectSubmitting] = useState(false);

  useLayoutEffect(() => {
    const measureBadgeOverflow = () => {
      const nextOverflow: Record<string, boolean> = {};
      model.badges.forEach((badge, index) => {
        const badgeId = `${badge.key}-${index}`;
        const element = badgeRefs.current[badgeId];
        nextOverflow[badgeId] = Boolean(
          element && element.scrollWidth - element.clientWidth > 1
        );
      });

      setBadgeOverflow((current) => {
        const currentKeys = Object.keys(current);
        const nextKeys = Object.keys(nextOverflow);
        if (
          currentKeys.length === nextKeys.length &&
          nextKeys.every((key) => current[key] === nextOverflow[key])
        ) {
          return current;
        }
        return nextOverflow;
      });
    };

    measureBadgeOverflow();

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measureBadgeOverflow);
      return () => window.removeEventListener('resize', measureBadgeOverflow);
    }

    const observer = new ResizeObserver(measureBadgeOverflow);
    model.badges.forEach((badge, index) => {
      const badgeId = `${badge.key}-${index}`;
      const element = badgeRefs.current[badgeId];
      if (element) {
        observer.observe(element);
      }
    });

    return () => observer.disconnect();
  }, [model.badges]);

  useLayoutEffect(() => {
    const measureTitleOverflow = () => {
      const element = titleLineRef.current;
      const nextOverflow = Boolean(
        element && element.scrollWidth - element.clientWidth > 1
      );
      setTitleOverflow((current) => (current === nextOverflow ? current : nextOverflow));
    };

    measureTitleOverflow();

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measureTitleOverflow);
      return () => window.removeEventListener('resize', measureTitleOverflow);
    }

    const observer = new ResizeObserver(measureTitleOverflow);
    if (titleLineRef.current) {
      observer.observe(titleLineRef.current);
    }

    return () => observer.disconnect();
  }, [item.title, item.author, isRequestDetailsOpen, isRequestRejectOpen]);

  const reviewRecord = item.requestRecord;
  const reviewApproveHandler = onRequestReviewApprove || onRequestApprove;
  const isDetailsExpanded = isRequestDetailsOpen || isRequestRejectOpen;

  useEffect(() => {
    if (!isRequestDetailsOpen) {
      setIsReviewSubmitting(false);
      return;
    }
  }, [isRequestDetailsOpen, reviewRecord?.id, reviewRecord?.updated_at]);

  useEffect(() => {
    if (!isRequestRejectOpen) {
      setRejectNote('');
      setIsRejectSubmitting(false);
      return;
    }
    setRejectNote('');
  }, [isRequestRejectOpen, reviewRecord?.id, reviewRecord?.updated_at]);

  const runAction = (action: ActivityCardAction) => {
    switch (action.kind) {
      case 'download-remove':
      case 'download-stop':
        onDownloadCancel?.(action.bookId);
        break;
      case 'download-dismiss':
        onDownloadDismiss?.(action.bookId, action.linkedRequestId);
        break;
      case 'request-approve':
        if (showRequestDetailsToggle && hasAttachedReleaseData(action.record)) {
          if (!isRequestDetailsOpen) {
            if (onRequestDetailsOpen) {
              onRequestDetailsOpen();
            } else if (onRequestDetailsToggle) {
              onRequestDetailsToggle();
            }
          }
          break;
        }
        onRequestApprove?.(action.requestId, action.record);
        break;
      case 'request-reject':
        onRequestReject?.(action.requestId);
        break;
      case 'request-cancel':
        onRequestCancel?.(action.requestId);
        break;
      case 'request-dismiss':
        onRequestDismiss?.(action.requestId);
        break;
      default:
        break;
    }
  };

  const hasActionHandler = (action: ActivityCardAction): boolean => {
    switch (action.kind) {
      case 'download-remove':
      case 'download-stop':
        return Boolean(onDownloadCancel);
      case 'download-dismiss':
        return Boolean(onDownloadDismiss);
      case 'request-approve':
        return Boolean(onRequestApprove);
      case 'request-reject':
        return Boolean(onRequestReject);
      case 'request-cancel':
        return Boolean(onRequestCancel);
      case 'request-dismiss':
        return Boolean(onRequestDismiss);
      default:
        return false;
    }
  };

  const actions = model.actions.filter(hasActionHandler);

  const bookData = asRecord(reviewRecord?.book_data);
  const releaseData = asRecord(reviewRecord?.release_data);
  const bookTitle = toOptionalText(bookData.title) || 'Unknown title';
  const fileTitle = toOptionalText(releaseData.title) || bookTitle;
  const fileFormat =
    toOptionalText(releaseData.format) ||
    toOptionalText(releaseData.filetype) ||
    toOptionalText(releaseData.extension) ||
    'Unknown';
  const fileSize = toOptionalText(releaseData.size) || 'Unknown';
  const sourceLabel = toSourceLabel(
    releaseData.source_display_name || releaseData.source || reviewRecord?.source_hint
  );

  const hasAttachedRelease =
    reviewRecord?.request_level === 'release' && Object.keys(releaseData).length > 0;
  const requiresBrowseBeforeApprove =
    reviewRecord?.request_level === 'book' || !hasAttachedRelease;
  const showSourceField = reviewRecord?.request_level === 'release';
  const isRetryAfterFailure = Boolean(toOptionalText(reviewRecord?.last_failure_reason));

  const approveLabel =
    requiresBrowseBeforeApprove
      ? isRetryAfterFailure
        ? 'Browse Releases To Retry'
        : 'Browse Releases To Approve'
      : 'Approve Attached File';
  const canMarkAsApprovedWithoutRelease = requiresBrowseBeforeApprove && !hasAttachedRelease;

  const provider = toOptionalText(bookData.provider)?.toLowerCase();
  const providerId = toOptionalText(bookData.provider_id);
  const canBrowseAlternatives = Boolean(provider && providerId);

  const handleReviewApprove = async () => {
    if (!reviewRecord || !reviewApproveHandler || isReviewSubmitting) {
      return;
    }

    setIsReviewSubmitting(true);
    try {
      if (requiresBrowseBeforeApprove) {
        await reviewApproveHandler(reviewRecord.id, reviewRecord, { browseOnly: true });
        return;
      }

      await reviewApproveHandler(reviewRecord.id, reviewRecord);
    } finally {
      setIsReviewSubmitting(false);
    }
  };

  const handleReviewBrowseAlternatives = async () => {
    if (!reviewRecord || !reviewApproveHandler || isReviewSubmitting) {
      return;
    }

    setIsReviewSubmitting(true);
    try {
      await reviewApproveHandler(reviewRecord.id, reviewRecord, { browseOnly: true });
    } finally {
      setIsReviewSubmitting(false);
    }
  };

  const handleReviewManualApproval = async () => {
    if (!reviewRecord || !reviewApproveHandler || isReviewSubmitting) {
      return;
    }

    setIsReviewSubmitting(true);
    try {
      await reviewApproveHandler(reviewRecord.id, reviewRecord, { manualApproval: true });
    } finally {
      setIsReviewSubmitting(false);
    }
  };

  const canShowInlineReview = Boolean(isRequestDetailsOpen && reviewRecord && reviewApproveHandler);
  const rejectConfirmHandler = onRequestRejectConfirm || onRequestReject;
  const canShowInlineReject = Boolean(
    isRequestRejectOpen &&
    item.requestId &&
    rejectConfirmHandler
  );
  const requestedAt = reviewRecord ? formatDateTime(reviewRecord.created_at) : '';
  const requestType = reviewRecord?.content_type === 'audiobook' ? 'Audiobook' : 'Book';
  const titleAuthorLine = item.author ? `${item.title} — ${item.author}` : item.title;
  const titleLineClassName = isDetailsExpanded
    ? 'text-sm leading-tight min-w-0 whitespace-normal break-words'
    : 'text-sm truncate leading-tight min-w-0';

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

  const handleInlineRejectConfirm = async () => {
    if (!item.requestId || !rejectConfirmHandler || isRejectSubmitting) {
      return;
    }

    setIsRejectSubmitting(true);
    try {
      const trimmed = rejectNote.trim();
      await rejectConfirmHandler(item.requestId, trimmed || undefined);
    } finally {
      setIsRejectSubmitting(false);
    }
  };

  return (
    <div
      className={`px-4 py-2 -mx-4 cursor-default ${
        isSelected ? 'relative' : 'hover-row'
      }`}
    >
      {isSelected && (
        <span
          aria-hidden="true"
          className="absolute left-0 top-2 bottom-2 w-1 bg-gray-400/80 dark:bg-gray-500/80"
        />
      )}
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
            <div className="flex-1 min-w-0">
              <Tooltip
                content={!isDetailsExpanded && titleOverflow ? titleAuthorLine : undefined}
                delay={0}
                position="bottom"
                triggerClassName="block max-w-full"
                alwaysWrap
              >
                <p ref={titleLineRef} className={titleLineClassName}>
                  <span className="font-semibold">{titleNode}</span>
                  {item.author && <span className="opacity-60 text-xs"> — {item.author}</span>}
                </p>
              </Tooltip>
            </div>
            <div className="flex-shrink-0 inline-flex items-center gap-1 -my-1">
              {actions.map((action) => {
                const config = actionUiConfig(action);
                const icon =
                  action.kind === 'request-approve' && isRetryAfterFailure
                    ? 'retry'
                    : config.icon;
                const actionTitle =
                  action.kind === 'request-approve' && isRetryAfterFailure
                    ? 'Retry'
                    : config.title;
                return (
                  <Tooltip
                    key={actionKey(action)}
                    content={actionTitle}
                    delay={0}
                    position="bottom"
                  >
                    <IconButton
                      title={actionTitle}
                      className={config.className}
                      onClick={() => runAction(action)}
                    >
                      <ActionIcon icon={icon} />
                    </IconButton>
                  </Tooltip>
                );
              })}
              {showRequestDetailsToggle && onRequestDetailsToggle && (
                <IconButton
                  title={isDetailsExpanded ? 'Hide details' : 'Show details'}
                  className="text-gray-500 hover-action"
                  onClick={onRequestDetailsToggle}
                >
                  <svg
                    className={`w-4 h-4 transition-transform ${isDetailsExpanded ? 'rotate-180' : ''}`}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    aria-hidden="true"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="m6 9 6 6 6-6" />
                  </svg>
                </IconButton>
              )}
            </div>
          </div>

          <p className="text-[11px] leading-tight opacity-60 truncate mt-0.5" title={item.metaLine}>
            {item.metaLine}
          </p>

          {noteLine && (
            <p className="text-[11px] opacity-60 italic truncate mt-0.5" title={noteLine}>
              {noteLine}
            </p>
          )}

          <div className="mt-1.5 flex items-center gap-2 min-w-0">
            {model.badges.map((badge, index) => {
              const badgeId = `${badge.key}-${index}`;
              const badgeStyle = STATUS_BADGE_STYLES[badge.visualStatus];
              const progressConfig = badge.isActiveDownload
                ? getProgressConfig(badge.visualStatus, badge.progress)
                : null;

              return (
                <Tooltip
                  key={badgeId}
                  content={badgeOverflow[badgeId] ? badge.text : undefined}
                  delay={0}
                  position="bottom"
                  unstyled
                  className={STATUS_TOOLTIP_CLASSES[badge.visualStatus]}
                >
                  <span
                    ref={(element) => {
                      if (element) {
                        badgeRefs.current[badgeId] = element;
                      } else {
                        delete badgeRefs.current[badgeId];
                      }
                    }}
                    className={`relative px-2 py-0.5 rounded-md text-[11px] font-medium truncate ${badgeStyle.bg} ${badgeStyle.text} ${badge.isActiveDownload ? 'flex-1 min-w-0' : 'inline-block max-w-full'}`}
                  >
                    {progressConfig && badgeStyle.fillColor && (
                      <span
                        className="absolute inset-y-0 left-0 rounded-md overflow-hidden transition-[width] duration-300"
                        style={{ width: `${progressConfig.percent}%` }}
                      >
                        <span
                          className="absolute inset-0 rounded-md"
                          style={{ backgroundColor: badgeStyle.fillColor }}
                        />
                        <span
                          className="absolute inset-0 rounded-md opacity-30 activity-wave"
                          style={{
                            background: 'linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.55) 50%, transparent 100%)',
                            backgroundSize: '200% 100%',
                          }}
                        />
                      </span>
                    )}
                    <span className="relative">{badge.text}</span>
                  </span>
                </Tooltip>
              );
            })}
          </div>

          {canShowInlineReview && (
            <div className="-mx-4 mt-2 px-4 pb-2 space-y-3 animate-fade-in">
              <div className={`grid grid-cols-1 ${showSourceField ? 'sm:grid-cols-3' : 'sm:grid-cols-2'} gap-x-3 gap-y-1`}>
                <DetailField label="Requested" value={requestedAt} />
                <DetailField label="Type" value={requestType} />
                {showSourceField && <DetailField label="Source" value={sourceLabel} />}
              </div>

              {hasAttachedRelease ? (
                <div className="space-y-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide opacity-70">Attached File</p>
                  <div className="grid grid-cols-1 gap-x-3 gap-y-1">
                    <DetailField label="Title" value={fileTitle} />
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1">
                    <DetailField label="Size" value={fileSize} />
                    <DetailField label="Format" value={String(fileFormat).toUpperCase()} />
                  </div>
                </div>
              ) : (
                <p className="text-xs opacity-70">
                  {reviewRecord?.request_level === 'book'
                    ? isRetryAfterFailure
                      ? 'Previous download failed. Choose a release before re-approving.'
                      : 'This is a book-level request without an attached file. Choose a release before approval.'
                    : isRetryAfterFailure
                      ? 'Previous download failed and the attached release was cleared. Choose a release before re-approving.'
                      : 'No attached release data is available. Choose a release before approval.'}
                </p>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={handleReviewApprove}
                  disabled={isReviewSubmitting}
                  className="px-2.5 py-1.5 rounded-md text-xs font-medium text-white bg-green-600 hover:bg-green-700 transition-colors disabled:opacity-60"
                >
                  {isReviewSubmitting ? 'Working...' : approveLabel}
                </button>
                {canMarkAsApprovedWithoutRelease && (
                  <button
                    type="button"
                    onClick={handleReviewManualApproval}
                    disabled={isReviewSubmitting}
                    className="px-2.5 py-1.5 rounded-md text-xs border border-[var(--border-muted)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50"
                  >
                    {isReviewSubmitting ? 'Working...' : 'Manually Mark as Approved'}
                  </button>
                )}
                {canBrowseAlternatives && hasAttachedRelease && (
                  <button
                    type="button"
                    onClick={handleReviewBrowseAlternatives}
                    disabled={isReviewSubmitting}
                    className="px-2.5 py-1.5 rounded-md text-xs border border-[var(--border-muted)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50"
                  >
                    Browse Alternatives
                  </button>
                )}
              </div>
            </div>
          )}

          {canShowInlineReject && (
            <div className="-mx-4 mt-2 px-4 pb-2 space-y-3 animate-fade-in">
              <p className="text-xs font-medium">
                Reject request for <span className="opacity-80">{item.title || 'Untitled request'}</span>
              </p>
              <textarea
                value={rejectNote}
                onChange={(event) => setRejectNote(event.target.value.slice(0, MAX_ADMIN_NOTE_LENGTH))}
                rows={3}
                maxLength={MAX_ADMIN_NOTE_LENGTH}
                placeholder="Optional note shown to the user"
                className="w-full px-2.5 py-2 rounded-md border border-[var(--border-muted)] bg-[var(--bg)] text-xs resize-y min-h-[72px] focus:outline-none focus:ring-2 focus:ring-red-500/30 focus:border-red-500"
                disabled={isRejectSubmitting}
              />
              <div className="flex items-center justify-between">
                <span className="text-[11px] opacity-60">{rejectNote.length}/{MAX_ADMIN_NOTE_LENGTH}</span>
                <div className="inline-flex items-center gap-2">
                  <button
                    type="button"
                    onClick={onRequestRejectClose}
                    disabled={isRejectSubmitting}
                    className="px-2.5 py-1.5 rounded-md text-xs hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleInlineRejectConfirm}
                    disabled={isRejectSubmitting}
                    className="px-2.5 py-1.5 rounded-md text-xs font-medium text-white bg-red-600 hover:bg-red-700 transition-colors disabled:opacity-60"
                  >
                    {isRejectSubmitting ? 'Rejecting...' : 'Reject'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

    </div>
  );
};
