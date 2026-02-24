import { useEffect, useMemo } from 'react';
import { DropdownList } from '../../DropdownList';
import { RequestPolicyMode } from '../../../types';
import {
  areRuleSetsEqual,
  getAllowedMatrixModes,
  getEffectiveCellMode,
  getInheritedCellMode,
  isMatrixConfigurable,
  normalizeExplicitRulesForPersistence,
  normalizeRequestPolicyRules,
  REQUEST_POLICY_DEFAULT_OPTIONS,
  REQUEST_POLICY_MODE_LABELS,
  RequestPolicyContentType,
  RequestPolicyDefaultsValue,
  RequestPolicyRuleRow,
  RequestPolicySourceCapability,
} from './requestPolicyGridUtils';

interface RequestPolicyGridProps {
  defaultModes: RequestPolicyDefaultsValue;
  onDefaultModeChange: (contentType: RequestPolicyContentType, mode: RequestPolicyMode) => void;
  onDefaultModeReset?: (contentType: RequestPolicyContentType) => void;
  defaultModeOverrides?: Partial<Record<RequestPolicyContentType, boolean>>;
  defaultModeDisabled?: Partial<Record<RequestPolicyContentType, boolean>>;
  explicitRules: RequestPolicyRuleRow[];
  baseRules?: RequestPolicyRuleRow[];
  onExplicitRulesChange: (rules: RequestPolicyRuleRow[]) => void;
  sourceCapabilities: RequestPolicySourceCapability[];
  rulesDisabled?: boolean;
  showClearOverrides?: boolean;
  onClearOverrides?: () => void;
  clearOverridesDisabled?: boolean;
}

const CONTENT_TYPES: RequestPolicyContentType[] = ['ebook', 'audiobook'];

const formatSourceLabel = (source: string): string => {
  return source
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

const toRuleKey = (source: string, contentType: RequestPolicyContentType) => `${source}::${contentType}`;

const modeDescriptions: Record<RequestPolicyMode, string> = {
  download: 'Users can download directly.',
  request_release: 'Users pick a release and request it.',
  request_book: 'Users can request a book, admin picks the release.',
  blocked: 'Downloads and requests are blocked.',
};

export const RequestPolicyGrid = ({
  defaultModes,
  onDefaultModeChange,
  onDefaultModeReset,
  defaultModeOverrides,
  defaultModeDisabled,
  explicitRules,
  baseRules = [],
  onExplicitRulesChange,
  sourceCapabilities,
  rulesDisabled = false,
  showClearOverrides = false,
  onClearOverrides,
  clearOverridesDisabled = false,
}: RequestPolicyGridProps) => {
  const normalizedExplicitRules = useMemo(
    () =>
      normalizeExplicitRulesForPersistence({
        explicitRules: normalizeRequestPolicyRules(explicitRules),
        baseRules,
        defaultModes,
        sourceCapabilities,
      }),
    [explicitRules, baseRules, defaultModes, sourceCapabilities]
  );

  useEffect(() => {
    if (!areRuleSetsEqual(normalizedExplicitRules, normalizeRequestPolicyRules(explicitRules))) {
      onExplicitRulesChange(normalizedExplicitRules);
    }
  }, [normalizedExplicitRules, explicitRules, onExplicitRulesChange]);

  const explicitRuleMap = useMemo(() => {
    const map = new Map<string, RequestPolicyRuleRow>();
    normalizedExplicitRules.forEach((rule) => {
      map.set(toRuleKey(rule.source, rule.content_type), rule);
    });
    return map;
  }, [normalizedExplicitRules]);

  const sourceRows = sourceCapabilities.map((sourceCapability) => ({
    ...sourceCapability,
    displayName: sourceCapability.displayName || formatSourceLabel(sourceCapability.source),
  }));

  const hasConfigurableColumn = CONTENT_TYPES.some((contentType) =>
    isMatrixConfigurable(defaultModes[contentType])
  );

  const updateCellRule = (
    source: string,
    contentType: RequestPolicyContentType,
    nextMode: RequestPolicyMode
  ) => {
    const inheritedMode = getInheritedCellMode(source, contentType, defaultModes, baseRules);
    const nextExplicitRules = normalizedExplicitRules.filter(
      (rule) => !(rule.source === source && rule.content_type === contentType)
    );

    if (nextMode !== inheritedMode) {
      nextExplicitRules.push({
        source,
        content_type: contentType,
        mode: nextMode as RequestPolicyRuleRow['mode'],
      });
    }

    const normalized = normalizeExplicitRulesForPersistence({
      explicitRules: nextExplicitRules,
      baseRules,
      defaultModes,
      sourceCapabilities,
    });
    onExplicitRulesChange(normalized);
  };

  const resetCellRule = (source: string, contentType: RequestPolicyContentType) => {
    const nextRules = normalizedExplicitRules.filter(
      (rule) => !(rule.source === source && rule.content_type === contentType)
    );
    onExplicitRulesChange(nextRules);
  };

  return (
    <div className="space-y-3">
      {showClearOverrides && onClearOverrides && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClearOverrides}
            disabled={clearOverridesDisabled}
            className="px-3 py-1.5 rounded-lg text-xs font-medium border border-[var(--border-muted)] bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Clear all overrides
          </button>
        </div>
      )}

      <div className="rounded-lg border border-[var(--border-muted)]">
        {/* Header */}
        <div className="hidden sm:grid sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 px-3 py-2 bg-[var(--bg-soft)] text-xs font-medium opacity-60 border-b border-[var(--border-muted)] rounded-t-lg">
          <span>Source</span>
          <span>Ebook</span>
          <span>Audiobook</span>
        </div>

        {/* Default row */}
        <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 px-3 py-2.5 items-center bg-[var(--bg-soft)] border-b-2 border-[var(--border-muted)]">
          <div className="min-w-0">
            <p className="text-sm font-semibold truncate">Default</p>
          </div>

          {CONTENT_TYPES.map((contentType) => {
            const mode = defaultModes[contentType];
            const isOverridden = Boolean(defaultModeOverrides?.[contentType]);
            const isDisabled = Boolean(defaultModeDisabled?.[contentType]);

            const mobileLabel = (
              <span className="sm:hidden text-xs font-medium opacity-50 mr-2">
                {contentType === 'ebook' ? 'Ebook:' : 'Audiobook:'}
              </span>
            );

            return (
              <div key={contentType} className="flex items-center gap-1.5">
                {mobileLabel}
                {isDisabled ? (
                  <div className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] text-sm opacity-60 cursor-not-allowed">
                    {REQUEST_POLICY_MODE_LABELS[mode]}
                  </div>
                ) : (
                  <div
                    className={`min-w-0 flex-1 ${
                      isOverridden ? 'rounded-lg ring-1 ring-sky-500/40' : ''
                    }`}
                  >
                    <DropdownList
                      options={REQUEST_POLICY_DEFAULT_OPTIONS}
                      value={mode}
                      onChange={(value) =>
                        onDefaultModeChange(
                          contentType,
                          (Array.isArray(value) ? value[0] : value) as RequestPolicyMode
                        )
                      }
                      widthClassName="w-full"
                    />
                  </div>
                )}
                {isOverridden && onDefaultModeReset && (
                  <button
                    type="button"
                    onClick={() => onDefaultModeReset(contentType)}
                    disabled={isDisabled}
                    className="text-xs text-sky-500 hover:text-sky-400 transition-colors shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Reset
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* Source rows */}
        {hasConfigurableColumn ? (
          sourceRows.map((sourceRow, index) => (
            <div
              key={sourceRow.source}
              className={`grid grid-cols-1 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 px-3 py-2.5 items-center ${
                index > 0 ? 'border-t border-[var(--border-muted)]' : ''
              }`}
            >
              <div className="min-w-0">
                <p className="text-sm font-medium truncate">{sourceRow.displayName}</p>
              </div>

              {CONTENT_TYPES.map((contentType) => {
                const key = toRuleKey(sourceRow.source, contentType);
                const isSupported = sourceRow.supportedContentTypes.includes(contentType);
                const defaultMode = defaultModes[contentType];
                const isConfigurable = isMatrixConfigurable(defaultMode);
                const effectiveMode = getEffectiveCellMode(
                  sourceRow.source,
                  contentType,
                  defaultModes,
                  baseRules,
                  normalizedExplicitRules
                );
                const explicitRule = explicitRuleMap.get(key);
                const isOverridden = Boolean(explicitRule);

                const mobileLabel = (
                  <span className="sm:hidden text-xs font-medium opacity-50 mr-2">
                    {contentType === 'ebook' ? 'Ebook:' : 'Audiobook:'}
                  </span>
                );

                if (!isSupported) {
                  return (
                    <div key={key} className="flex items-center justify-center min-h-[36px]">
                      {mobileLabel}
                      <span className="text-xs opacity-40">Not supported</span>
                    </div>
                  );
                }

                if (!isConfigurable) {
                  return (
                    <div key={key} className="flex items-center justify-center min-h-[36px]">
                      {mobileLabel}
                      <span className="text-xs opacity-40">
                        {REQUEST_POLICY_MODE_LABELS[effectiveMode]}
                      </span>
                    </div>
                  );
                }

                const allowedModes = getAllowedMatrixModes(defaultMode);
                // When the effective mode isn't an allowed matrix mode (e.g. request_book
                // as a ceiling default), include it as the first option so the dropdown
                // shows the current state and lets the user switch away from it.
                const effectiveModeOption =
                  !allowedModes.includes(effectiveMode as typeof allowedModes[number])
                    ? [{ value: effectiveMode, label: REQUEST_POLICY_MODE_LABELS[effectiveMode], description: modeDescriptions[effectiveMode] }]
                    : [];
                const options = [
                  ...effectiveModeOption,
                  ...allowedModes.map((mode) => ({
                    value: mode,
                    label: REQUEST_POLICY_MODE_LABELS[mode],
                    description: modeDescriptions[mode],
                  })),
                ];

                return (
                  <div key={key} className="flex items-center gap-1.5">
                    {mobileLabel}
                    <div
                      className={`min-w-0 flex-1 ${
                        isOverridden ? 'rounded-lg ring-1 ring-sky-500/40' : ''
                      }`}
                    >
                      {rulesDisabled ? (
                        <div className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm opacity-60 cursor-not-allowed">
                          {REQUEST_POLICY_MODE_LABELS[effectiveMode]}
                        </div>
                      ) : (
                        <DropdownList
                          options={options}
                          value={effectiveMode}
                          onChange={(value) => {
                            const nextMode = (Array.isArray(value) ? value[0] : value) as RequestPolicyMode;
                            updateCellRule(sourceRow.source, contentType, nextMode);
                          }}
                          widthClassName="w-full"
                        />
                      )}
                    </div>
                    {isOverridden && !rulesDisabled && (
                      <button
                        type="button"
                        onClick={() => resetCellRule(sourceRow.source, contentType)}
                        className="text-xs text-sky-500 hover:text-sky-400 transition-colors shrink-0"
                        aria-label={`Reset ${sourceRow.displayName} ${contentType} override`}
                      >
                        Reset
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          ))
        ) : (
          <div className="px-3 py-3">
            <p className="text-xs opacity-60">
              Per-source settings become available when a default is set to Download or Request Release.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
