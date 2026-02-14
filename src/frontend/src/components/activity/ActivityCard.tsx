import { ReactNode, useLayoutEffect, useMemo, useRef, useState } from 'react';
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

interface ActivityCardProps {
  item: ActivityItem;
  isAdmin: boolean;
  onDownloadCancel?: (bookId: string) => void;
  onRequestCancel?: (requestId: number) => void;
  onRequestApprove?: (requestId: number, record: RequestRecord) => void;
  onRequestReject?: (requestId: number) => void;
  onRequestDismiss?: (requestId: number) => void;
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
): { title: string; className: string; icon: 'cross' | 'check' | 'stop' } => {
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

const ActionIcon = ({ icon }: { icon: 'cross' | 'check' | 'stop' }) => {
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
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
};

export const ActivityCard = ({
  item,
  isAdmin,
  onDownloadCancel,
  onRequestCancel,
  onRequestApprove,
  onRequestReject,
  onRequestDismiss,
}: ActivityCardProps) => {
  const model = useMemo(() => buildActivityCardModel(item, isAdmin), [item, isAdmin]);
  const noteLine = model.noteLine;
  const badgeRefs = useRef<Record<string, HTMLSpanElement | null>>({});
  const [badgeOverflow, setBadgeOverflow] = useState<Record<string, boolean>>({});

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

  const runAction = (action: ActivityCardAction) => {
    switch (action.kind) {
      case 'download-remove':
      case 'download-stop':
        onDownloadCancel?.(action.bookId);
        break;
      case 'download-dismiss':
        onDownloadCancel?.(action.bookId);
        if (action.linkedRequestId) {
          onRequestDismiss?.(action.linkedRequestId);
        }
        break;
      case 'request-approve':
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
      case 'download-dismiss':
        return Boolean(onDownloadCancel);
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
    <div className="px-4 py-2 -mx-4 hover-row cursor-default">
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
            <div className="flex-shrink-0 inline-flex items-center gap-1 -my-1">
              {actions.map((action) => {
                const config = actionUiConfig(action);
                return (
                  <Tooltip
                    key={actionKey(action)}
                    content={config.title}
                    delay={0}
                    position="bottom"
                  >
                    <IconButton
                      title={config.title}
                      className={config.className}
                      onClick={() => runAction(action)}
                    >
                      <ActionIcon icon={config.icon} />
                    </IconButton>
                  </Tooltip>
                );
              })}
            </div>
          </div>

          <p className="text-xs opacity-60 truncate mt-0.5" title={item.metaLine}>
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
        </div>
      </div>

    </div>
  );
};
