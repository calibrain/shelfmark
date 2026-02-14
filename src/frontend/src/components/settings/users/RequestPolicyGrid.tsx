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
  download: 'Direct downloads allowed.',
  request_release: 'Specific release requests only.',
  request_book: 'Book-level requests only.',
  blocked: 'Unavailable.',
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
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {CONTENT_TYPES.map((contentType) => {
          const mode = defaultModes[contentType];
          const isOverridden = Boolean(defaultModeOverrides?.[contentType]);
          const isDisabled = Boolean(defaultModeDisabled?.[contentType]);
          const label = contentType === 'ebook' ? 'Default Ebook Mode' : 'Default Audiobook Mode';

          return (
            <div
              key={contentType}
              className={`rounded-lg border p-3 ${
                isOverridden
                  ? 'border-sky-500/50 bg-sky-500/5'
                  : 'border-[var(--border-muted)] bg-[var(--bg-soft)]'
              }`}
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-sm font-medium">{label}</p>
                {isOverridden && onDefaultModeReset && (
                  <button
                    type="button"
                    onClick={() => onDefaultModeReset(contentType)}
                    disabled={isDisabled}
                    className="px-2 py-1 rounded text-xs border border-[var(--border-muted)] bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Reset
                  </button>
                )}
              </div>
              {isDisabled ? (
                <div className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] text-sm opacity-60 cursor-not-allowed">
                  {REQUEST_POLICY_MODE_LABELS[mode]}
                </div>
              ) : (
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
              )}
            </div>
          );
        })}
      </div>

      {showClearOverrides && onClearOverrides && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClearOverrides}
            disabled={clearOverridesDisabled}
            className="px-3 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)] bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Clear all request overrides
          </button>
        </div>
      )}

      <div className="rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] p-3">
        <p className="text-sm font-medium mb-2">Per-source overrides</p>
        {!hasConfigurableColumn ? (
          <p className="text-xs opacity-70">
            Per-source rules only apply when the content-type default is Download or Request Release.
          </p>
        ) : (
          <div className="space-y-2">
            <div className="hidden sm:grid sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 text-xs font-medium opacity-70 px-2">
              <span>Source</span>
              <span>Ebook</span>
              <span>Audiobook</span>
            </div>

            {sourceRows.map((sourceRow) => (
              <div
                key={sourceRow.source}
                className="grid grid-cols-1 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] p-2"
              >
                <div className="flex items-center min-w-0">
                  <p className="text-sm font-medium truncate">{sourceRow.displayName}</p>
                </div>

                {CONTENT_TYPES.map((contentType) => {
                  const key = toRuleKey(sourceRow.source, contentType);
                  const isSupported = sourceRow.supportedContentTypes.includes(contentType);
                  const defaultMode = defaultModes[contentType];
                  const isConfigurable = isMatrixConfigurable(defaultMode);
                  const inheritedMode = getInheritedCellMode(
                    sourceRow.source,
                    contentType,
                    defaultModes,
                    baseRules
                  );
                  const effectiveMode = getEffectiveCellMode(
                    sourceRow.source,
                    contentType,
                    defaultModes,
                    baseRules,
                    normalizedExplicitRules
                  );
                  const explicitRule = explicitRuleMap.get(key);
                  const isOverridden = Boolean(explicitRule);

                  if (!isSupported) {
                    return (
                      <div
                        key={key}
                        className="rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] px-3 py-2 text-xs opacity-60"
                      >
                        Not supported
                      </div>
                    );
                  }

                  if (!isConfigurable) {
                    return (
                      <div
                        key={key}
                        className="rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] px-3 py-2 text-xs"
                      >
                        <span className="font-medium">{REQUEST_POLICY_MODE_LABELS[effectiveMode]}</span>
                        <span className="ml-1 opacity-60">(from default)</span>
                      </div>
                    );
                  }

                  const allowedModes = getAllowedMatrixModes(defaultMode);
                  const options = allowedModes.map((mode) => ({
                    value: mode,
                    label: REQUEST_POLICY_MODE_LABELS[mode],
                    description: modeDescriptions[mode],
                  }));

                  return (
                    <div
                      key={key}
                      className={`rounded-lg border p-1 ${
                        isOverridden
                          ? 'border-sky-500/50 bg-sky-500/5'
                          : 'border-[var(--border-muted)] bg-[var(--bg-soft)]'
                      }`}
                    >
                      <div className="flex items-center gap-1">
                        <div className="min-w-0 flex-1">
                          {rulesDisabled ? (
                            <div className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] text-sm opacity-60 cursor-not-allowed">
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
                        {isOverridden && (
                          <button
                            type="button"
                            onClick={() => resetCellRule(sourceRow.source, contentType)}
                            disabled={rulesDisabled}
                            className="px-2 py-1 rounded text-xs border border-[var(--border-muted)] bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            aria-label={`Reset ${sourceRow.displayName} ${contentType} override`}
                          >
                            Reset
                          </button>
                        )}
                      </div>
                      {!isOverridden && inheritedMode !== effectiveMode && (
                        <p className="mt-1 text-[11px] opacity-60">
                          Inherits {REQUEST_POLICY_MODE_LABELS[inheritedMode]}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
