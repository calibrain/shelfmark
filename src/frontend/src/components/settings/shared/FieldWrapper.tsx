import { ReactNode } from 'react';
import { SettingsField } from '../../../types/settings';
import { Tooltip } from '../../shared/Tooltip';
import { EnvLockBadge } from './EnvLockBadge';

const MARKDOWN_LINK_PATTERN = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;

const renderDescriptionWithLinks = (description: string): ReactNode => {
  const parts: ReactNode[] = [];
  let lastIndex = 0;

  MARKDOWN_LINK_PATTERN.lastIndex = 0;
  let match: RegExpExecArray | null = MARKDOWN_LINK_PATTERN.exec(description);
  while (match) {
    const [fullMatch, label, url] = match;
    const matchIndex = match.index;

    if (matchIndex > lastIndex) {
      parts.push(
        <span key={`text-${lastIndex}-${matchIndex}`} className="opacity-60">
          {description.slice(lastIndex, matchIndex)}
        </span>
      );
    }

    parts.push(
      <a
        key={`${url}-${matchIndex}`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="underline text-sky-600 dark:text-sky-400"
      >
        {label}
      </a>
    );

    lastIndex = matchIndex + fullMatch.length;
    match = MARKDOWN_LINK_PATTERN.exec(description);
  }

  if (lastIndex < description.length) {
    parts.push(
      <span key={`text-${lastIndex}-end`} className="opacity-60">
        {description.slice(lastIndex)}
      </span>
    );
  }

  if (parts.length === 0) {
    return <span className="opacity-60">{description}</span>;
  }

  return parts;
};

interface FieldWrapperProps {
  field: SettingsField;
  children: ReactNode;
  // Optional overrides for dynamic disabled state (from disabledWhen)
  disabledOverride?: boolean;
  disabledReasonOverride?: string;
  resetAction?: {
    label?: string;
    disabled?: boolean;
    onClick: () => void;
  };
  headerRight?: ReactNode;
  userOverrideCount?: number;
  userOverrideDetails?: Array<{
    userId: number;
    username: string;
    value: unknown;
  }>;
}

// Badge shown when a field is disabled
const DisabledBadge = ({ reason }: { reason?: string }) => (
  <span
    className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded
               bg-zinc-500/20 text-zinc-400 border border-zinc-500/30"
    title={reason || 'This setting is not available'}
  >
    <svg
      className="w-3 h-3"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.5}
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
      />
    </svg>
    Unavailable
  </span>
);

// Badge shown when changing a field requires a container restart
const RestartRequiredBadge = () => (
  <span
    className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded
               bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30"
    title="Changing this setting requires a container restart to take effect"
  >
    <svg
      className="w-3 h-3"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.5}
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"
      />
    </svg>
    Restart
  </span>
);

const UserOverriddenBadge = ({
  count,
  details = [],
}: {
  count: number;
  details?: Array<{ userId: number; username: string; value: unknown }>;
}) => {
  const formatValue = (value: unknown): string => {
    if (value === null || value === undefined) return '(empty)';
    if (typeof value === 'string') return value || '(empty)';
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  };

  const visibleDetails = details.slice(0, 10);
  const extraCount = Math.max(details.length - visibleDetails.length, 0);

  const content = (
    <div className="space-y-1 max-w-xs">
      {visibleDetails.map((entry) => (
        <div key={entry.userId} className="text-[11px] leading-snug">
          <span className="font-medium">{entry.username}</span>
          <span className="opacity-70">: {formatValue(entry.value)}</span>
        </div>
      ))}
      {extraCount > 0 && (
        <div className="text-[11px] opacity-70">and {extraCount} more...</div>
      )}
    </div>
  );

  return (
    <Tooltip content={content} position="top">
      <span
        className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded
                   bg-sky-500/15 text-sky-500 dark:text-sky-400 border border-sky-500/30"
      >
        User overridden{count > 1 ? ` (${count})` : ''}
      </span>
    </Tooltip>
  );
};

const ResetActionButton = ({
  label = 'Reset',
  disabled = false,
  onClick,
}: {
  label?: string;
  disabled?: boolean;
  onClick: () => void;
}) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className="text-xs font-medium text-sky-500 hover:text-sky-400 transition-colors shrink-0
               disabled:opacity-50 disabled:cursor-not-allowed"
  >
    {label}
  </button>
);

export const FieldWrapper = ({
  field,
  children,
  disabledOverride,
  disabledReasonOverride,
  resetAction,
  headerRight,
  userOverrideCount,
  userOverrideDetails,
}: FieldWrapperProps) => {
  // Action buttons and headings handle their own layout
  if (field.type === 'ActionButton' || field.type === 'HeadingField') {
    return <>{children}</>;
  }

  // At this point, field is a regular input field with standard properties
  // Use overrides if provided (from disabledWhen), otherwise use field's static values
  const isDisabled = disabledOverride ?? field.disabled;
  const disabledReason = disabledReasonOverride ?? field.disabledReason;
  const requiresRestart = field.requiresRestart;
  const hasUserOverrides = Boolean(userOverrideCount) && (userOverrideCount || 0) > 0;
  const hasLabel = Boolean(field.label && field.label.trim().length > 0);
  const hasResetAction = Boolean(resetAction);
  const showHeaderLeft = hasLabel || field.fromEnv || (requiresRestart && !isDisabled && !field.fromEnv)
    || (isDisabled && !field.fromEnv) || hasUserOverrides;
  const showHeader = showHeaderLeft || hasResetAction || Boolean(headerRight);

  // ENV-locked fields should only dim the control, not the label/description
  const isFullyDimmed = isDisabled && !field.fromEnv;
  return (
    <div className="space-y-1.5">
      {showHeader && (
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap min-w-0">
            {hasLabel && (
              <label className={`text-sm font-medium ${isFullyDimmed ? 'text-zinc-500' : ''}`}>
                {field.label}
                {field.required && !isDisabled && <span className="text-red-500 ml-0.5">*</span>}
              </label>
            )}
            {field.fromEnv && <EnvLockBadge />}
            {requiresRestart && !isDisabled && !field.fromEnv && <RestartRequiredBadge />}
            {isDisabled && !field.fromEnv && <DisabledBadge reason={disabledReason} />}
            {hasUserOverrides && (
              <UserOverriddenBadge
                count={userOverrideCount || 0}
                details={userOverrideDetails}
              />
            )}
          </div>
          {(hasResetAction || headerRight) && (
            <div className="flex items-center gap-2 shrink-0">
              {hasResetAction && resetAction && (
                <ResetActionButton
                  label={resetAction.label}
                  disabled={resetAction.disabled}
                  onClick={resetAction.onClick}
                />
              )}
              {headerRight}
            </div>
          )}
        </div>
      )}

      <div className={isFullyDimmed ? 'opacity-50' : ''}>{children}</div>

      {field.description && (
        <p className="text-xs">{renderDescriptionWithLinks(field.description)}</p>
      )}

      {isDisabled && disabledReason && (
        <p className="text-xs text-zinc-400 italic">{disabledReason}</p>
      )}
    </div>
  );
};
