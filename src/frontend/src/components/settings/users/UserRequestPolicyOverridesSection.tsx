import {
  CheckboxFieldConfig,
  CustomComponentFieldConfig,
  HeadingFieldConfig,
  MultiSelectFieldConfig,
  NumberFieldConfig,
  SelectFieldConfig,
  SettingsTab,
  TableFieldConfig,
} from '../../../types/settings';
import {
  CheckboxField,
  HeadingField,
  MultiSelectField,
  NumberField,
  SelectField,
} from '../fields';
import { FieldWrapper } from '../shared';
import { getFieldByKey } from './fieldHelpers';
import { PerUserSettings } from './types';
import { RequestPolicyGrid } from './RequestPolicyGrid';
import {
  normalizeExplicitRulesForPersistence,
  normalizeRequestPolicyDefaults,
  normalizeRequestPolicyRules,
  parseSourceCapabilitiesFromRulesField,
} from './requestPolicyGridUtils';

interface UserRequestPolicyOverridesSectionProps {
  usersTab: SettingsTab;
  globalUsersSettingsValues: Record<string, unknown>;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
}

const ALL_REQUEST_OVERRIDE_KEYS: Array<keyof PerUserSettings> = [
  'REQUESTS_ENABLED',
  'REQUEST_POLICY_DEFAULT_EBOOK',
  'REQUEST_POLICY_DEFAULT_AUDIOBOOK',
  'REQUEST_POLICY_RULES',
  'MAX_PENDING_REQUESTS_PER_USER',
  'REQUESTS_ALLOW_NOTES',
  'REQUEST_AUTO_SELECT_ENABLED',
  'REQUEST_AUTO_APPROVE_ENABLED',
  'REQUEST_AUTO_PREFERRED_SOURCE',
  'REQUEST_AUTO_PREFERRED_INDEXER',
  'REQUEST_AUTO_CONTENT_TYPES',
  'REQUEST_AUTO_FORMATS',
  'REQUEST_AUTO_SELECTION_POLICY',
  'REQUEST_AUTO_FALLBACK_STRATEGY',
];

const ADVANCED_REQUEST_OVERRIDE_KEYS: Array<keyof PerUserSettings> = ALL_REQUEST_OVERRIDE_KEYS.filter(
  (key) => key !== 'REQUESTS_ENABLED'
);

const AUTO_SELECTION_OVERRIDE_KEYS: Array<keyof PerUserSettings> = [
  'REQUEST_AUTO_SELECT_ENABLED',
  'REQUEST_AUTO_APPROVE_ENABLED',
  'REQUEST_AUTO_PREFERRED_SOURCE',
  'REQUEST_AUTO_PREFERRED_INDEXER',
  'REQUEST_AUTO_CONTENT_TYPES',
  'REQUEST_AUTO_FORMATS',
  'REQUEST_AUTO_SELECTION_POLICY',
  'REQUEST_AUTO_FALLBACK_STRATEGY',
];

const requestPolicyHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'request_policy_overrides_heading',
  title: 'Requests',
  description: 'Custom request settings for this user. Reset any value to fall back to the global defaults.',
};

const autoSelectionHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'request_auto_selection_overrides_heading',
  title: 'Automatic Release Selection',
  description: 'Source, format, and ranking preferences used when Shelfmark auto-selects a release for this user.',
};

const fallbackRequestsEnabledField: CheckboxFieldConfig = {
  type: 'CheckboxField',
  key: 'REQUESTS_ENABLED',
  label: 'Enable Requests',
  description: 'Turn this off to disable the request workflow for this user.',
  value: false,
};

const fallbackMaxPendingField: NumberFieldConfig = {
  type: 'NumberField',
  key: 'MAX_PENDING_REQUESTS_PER_USER',
  label: 'Max pending requests per user',
  description: 'How many open requests this user can have at one time.',
  value: 20,
  min: 1,
  max: 1000,
  step: 1,
};

const fallbackAllowNotesField: CheckboxFieldConfig = {
  type: 'CheckboxField',
  key: 'REQUESTS_ALLOW_NOTES',
  label: 'Allow notes on requests',
  description: 'Let this user add a note when they submit a request.',
  value: true,
};

const fallbackAutoSelectField: CheckboxFieldConfig = {
  type: 'CheckboxField',
  key: 'REQUEST_AUTO_SELECT_ENABLED',
  label: 'Enable Automatic Release Selection',
  description: 'Let Shelfmark search for and pick a release automatically for this user.',
  value: false,
};

const fallbackAutoApproveField: CheckboxFieldConfig = {
  type: 'CheckboxField',
  key: 'REQUEST_AUTO_APPROVE_ENABLED',
  label: 'Allow Automatic Queueing',
  description: 'Queue automatically selected releases immediately when policy allows direct download.',
  value: false,
};

const fallbackPreferredSourceField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'REQUEST_AUTO_PREFERRED_SOURCE',
  label: 'Preferred Release Source',
  description: 'Optional source to search first for automatic release selection.',
  value: '',
  options: [],
  default: '',
};

const fallbackPreferredIndexerField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'REQUEST_AUTO_PREFERRED_INDEXER',
  label: 'Preferred Prowlarr Indexer',
  description: 'Optional tracker/indexer preference inside Prowlarr.',
  value: '',
  options: [{ value: '', label: 'Any indexer' }],
  default: '',
};

const fallbackAutoContentTypesField: MultiSelectFieldConfig = {
  type: 'MultiSelectField',
  key: 'REQUEST_AUTO_CONTENT_TYPES',
  label: 'Automatic Selection Content Types',
  description: 'Choose which request content types are eligible for automatic release selection.',
  value: ['ebook', 'audiobook'],
  options: [
    { value: 'ebook', label: 'Ebook' },
    { value: 'audiobook', label: 'Audiobook' },
  ],
  variant: 'dropdown',
};

const fallbackAutoFormatsField: MultiSelectFieldConfig = {
  type: 'MultiSelectField',
  key: 'REQUEST_AUTO_FORMATS',
  label: 'Preferred File Formats',
  description: 'Optional preferred formats for automatic release selection.',
  value: [],
  options: [],
  variant: 'dropdown',
};

const fallbackSelectionPolicyField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'REQUEST_AUTO_SELECTION_POLICY',
  label: 'Selection Policy',
  description: 'How matching releases are ranked.',
  value: 'best_match',
  options: [
    { value: 'best_match', label: 'Best Match' },
    { value: 'most_seeders', label: 'Most Seeders' },
    { value: 'best_availability', label: 'Best Availability' },
    { value: 'newest', label: 'Newest' },
  ],
  default: 'best_match',
};

const fallbackFallbackStrategyField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'REQUEST_AUTO_FALLBACK_STRATEGY',
  label: 'Fallback Strategy',
  description: 'How far Shelfmark can widen the search when the preferred source has no match.',
  value: 'same_source',
  options: [
    { value: 'same_source', label: 'Same Source Only' },
    { value: 'same_source_then_any_source', label: 'Same Source, Then Any' },
  ],
  default: 'same_source',
};

const hasOwnNonNull = (settings: PerUserSettings, key: keyof PerUserSettings): boolean => (
  Object.prototype.hasOwnProperty.call(settings, key)
  && settings[key] !== null
  && settings[key] !== undefined
);

const toBoolean = (value: unknown, fallback = false): boolean => {
  if (typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (['1', 'true', 'yes', 'on'].includes(normalized)) {
      return true;
    }
    if (['0', 'false', 'no', 'off', ''].includes(normalized)) {
      return false;
    }
  }
  if (value === undefined || value === null) {
    return fallback;
  }
  return Boolean(value);
};

const toNumber = (value: unknown, fallback: number): number => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const toStringValue = (value: unknown, fallback = ''): string => {
  if (value === undefined || value === null) {
    return fallback;
  }
  return String(value);
};

const toStringArray = (value: unknown): string[] => {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => String(entry ?? '').trim())
    .filter((entry) => entry.length > 0);
};

const normalizeComparableValue = (value: unknown): string => {
  if (value === null || value === undefined) {
    return '';
  }
  if (Array.isArray(value)) {
    return JSON.stringify(value.map((entry) => String(entry)));
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
};

export const UserRequestPolicyOverridesSection = ({
  usersTab,
  globalUsersSettingsValues,
  isUserOverridable,
  userSettings,
  setUserSettings,
}: UserRequestPolicyOverridesSectionProps) => {
  const requestPolicyEditorField = usersTab.fields.find(
    (field): field is CustomComponentFieldConfig =>
      field.key === 'request_policy_editor' && field.type === 'CustomComponentField'
  );
  const rulesField = requestPolicyEditorField?.boundFields?.find(
    (field): field is TableFieldConfig =>
      field.key === 'REQUEST_POLICY_RULES' && field.type === 'TableField'
  );
  const defaultEbookField = requestPolicyEditorField?.boundFields?.find(
    (field): field is SelectFieldConfig =>
      field.key === 'REQUEST_POLICY_DEFAULT_EBOOK' && field.type === 'SelectField'
  );
  const defaultAudioField = requestPolicyEditorField?.boundFields?.find(
    (field): field is SelectFieldConfig =>
      field.key === 'REQUEST_POLICY_DEFAULT_AUDIOBOOK' && field.type === 'SelectField'
  );

  const requestsEnabledField = getFieldByKey<CheckboxFieldConfig>(
    usersTab.fields,
    'REQUESTS_ENABLED',
    fallbackRequestsEnabledField
  );
  const maxPendingField = getFieldByKey<NumberFieldConfig>(
    usersTab.fields,
    'MAX_PENDING_REQUESTS_PER_USER',
    fallbackMaxPendingField
  );
  const allowNotesField = getFieldByKey<CheckboxFieldConfig>(
    usersTab.fields,
    'REQUESTS_ALLOW_NOTES',
    fallbackAllowNotesField
  );
  const autoSelectField = getFieldByKey<CheckboxFieldConfig>(
    usersTab.fields,
    'REQUEST_AUTO_SELECT_ENABLED',
    fallbackAutoSelectField
  );
  const autoApproveField = getFieldByKey<CheckboxFieldConfig>(
    usersTab.fields,
    'REQUEST_AUTO_APPROVE_ENABLED',
    fallbackAutoApproveField
  );
  const preferredSourceField = getFieldByKey<SelectFieldConfig>(
    usersTab.fields,
    'REQUEST_AUTO_PREFERRED_SOURCE',
    fallbackPreferredSourceField
  );
  const preferredIndexerField = getFieldByKey<SelectFieldConfig>(
    usersTab.fields,
    'REQUEST_AUTO_PREFERRED_INDEXER',
    fallbackPreferredIndexerField
  );
  const autoContentTypesField = getFieldByKey<MultiSelectFieldConfig>(
    usersTab.fields,
    'REQUEST_AUTO_CONTENT_TYPES',
    fallbackAutoContentTypesField
  );
  const autoFormatsField = getFieldByKey<MultiSelectFieldConfig>(
    usersTab.fields,
    'REQUEST_AUTO_FORMATS',
    fallbackAutoFormatsField
  );
  const selectionPolicyField = getFieldByKey<SelectFieldConfig>(
    usersTab.fields,
    'REQUEST_AUTO_SELECTION_POLICY',
    fallbackSelectionPolicyField
  );
  const fallbackStrategyField = getFieldByKey<SelectFieldConfig>(
    usersTab.fields,
    'REQUEST_AUTO_FALLBACK_STRATEGY',
    fallbackFallbackStrategyField
  );

  const isOverridden = (key: keyof PerUserSettings): boolean => (
    hasOwnNonNull(userSettings, key)
    && normalizeComparableValue(userSettings[key]) !== normalizeComparableValue(globalUsersSettingsValues[key])
  );

  const readValue = (key: keyof PerUserSettings): unknown => (
    hasOwnNonNull(userSettings, key) ? userSettings[key] : globalUsersSettingsValues[key]
  );

  const resetKeys = (keys: Array<keyof PerUserSettings>) => {
    setUserSettings((prev) => {
      const next = { ...prev };
      keys.forEach((key) => {
        delete next[key];
      });
      return next;
    });
  };

  const setOverride = (key: keyof PerUserSettings, value: unknown) => {
    setUserSettings((prev) => ({ ...prev, [key]: value }));
  };

  const requestsEnabledValue = toBoolean(readValue('REQUESTS_ENABLED'));
  const maxPendingValue = toNumber(readValue('MAX_PENDING_REQUESTS_PER_USER'), 20);
  const allowNotesValue = toBoolean(readValue('REQUESTS_ALLOW_NOTES'), true);
  const autoSelectEnabledValue = toBoolean(readValue('REQUEST_AUTO_SELECT_ENABLED'));
  const autoApproveValue = toBoolean(readValue('REQUEST_AUTO_APPROVE_ENABLED'));
  const preferredSourceValue = toStringValue(readValue('REQUEST_AUTO_PREFERRED_SOURCE'));
  const preferredIndexerValue = toStringValue(readValue('REQUEST_AUTO_PREFERRED_INDEXER'));
  const autoContentTypesValue = toStringArray(readValue('REQUEST_AUTO_CONTENT_TYPES'));
  const autoFormatsValue = toStringArray(readValue('REQUEST_AUTO_FORMATS'));
  const selectionPolicyValue = toStringValue(readValue('REQUEST_AUTO_SELECTION_POLICY'), 'best_match');
  const fallbackStrategyValue = toStringValue(
    readValue('REQUEST_AUTO_FALLBACK_STRATEGY'),
    'same_source'
  );

  const globalDefaults = normalizeRequestPolicyDefaults({
    ebook: globalUsersSettingsValues.REQUEST_POLICY_DEFAULT_EBOOK,
    audiobook: globalUsersSettingsValues.REQUEST_POLICY_DEFAULT_AUDIOBOOK,
  });
  const globalRules = normalizeRequestPolicyRules(globalUsersSettingsValues.REQUEST_POLICY_RULES);

  const hasUserEbookDefault = hasOwnNonNull(userSettings, 'REQUEST_POLICY_DEFAULT_EBOOK');
  const hasUserAudiobookDefault = hasOwnNonNull(userSettings, 'REQUEST_POLICY_DEFAULT_AUDIOBOOK');
  const explicitUserRules = normalizeRequestPolicyRules(userSettings.REQUEST_POLICY_RULES);

  const effectiveDefaults = normalizeRequestPolicyDefaults({
    ebook: hasUserEbookDefault ? userSettings.REQUEST_POLICY_DEFAULT_EBOOK : globalDefaults.ebook,
    audiobook: hasUserAudiobookDefault
      ? userSettings.REQUEST_POLICY_DEFAULT_AUDIOBOOK
      : globalDefaults.audiobook,
  });

  const sourceCapabilities = rulesField
    ? parseSourceCapabilitiesFromRulesField(rulesField, [
        ...globalRules.map((row) => row.source),
        ...explicitUserRules.map((row) => row.source),
      ])
    : [];

  const setRulesOverride = (
    nextRulesRaw: typeof explicitUserRules,
    nextDefaults = effectiveDefaults
  ) => {
    const normalized = normalizeExplicitRulesForPersistence({
      explicitRules: nextRulesRaw,
      baseRules: globalRules,
      defaultModes: nextDefaults,
      sourceCapabilities,
    });

    setUserSettings((prev) => {
      const next = { ...prev };
      if (normalized.length === 0) {
        delete next.REQUEST_POLICY_RULES;
      } else {
        next.REQUEST_POLICY_RULES = normalized as unknown as Array<Record<string, unknown>>;
      }
      return next;
    });
  };

  const hasAnyRequestOverrides = ALL_REQUEST_OVERRIDE_KEYS.some((key) => hasOwnNonNull(userSettings, key));
  const hasAnyAdvancedRequestOverrides = ADVANCED_REQUEST_OVERRIDE_KEYS.some((key) =>
    hasOwnNonNull(userSettings, key)
  );
  const hasAnyAutoSelectionOverrides = AUTO_SELECTION_OVERRIDE_KEYS.some((key) =>
    hasOwnNonNull(userSettings, key)
  );

  const canOverrideDefaults = (
    isUserOverridable('REQUEST_POLICY_DEFAULT_EBOOK')
    && isUserOverridable('REQUEST_POLICY_DEFAULT_AUDIOBOOK')
  );
  const canOverrideRules = isUserOverridable('REQUEST_POLICY_RULES');
  const canOverrideRequestToggle = isUserOverridable('REQUESTS_ENABLED');
  const canOverrideMaxPending = isUserOverridable('MAX_PENDING_REQUESTS_PER_USER');
  const canOverrideAllowNotes = isUserOverridable('REQUESTS_ALLOW_NOTES');
  const canOverrideAutoSelect = isUserOverridable('REQUEST_AUTO_SELECT_ENABLED');
  const canOverrideAutoApprove = isUserOverridable('REQUEST_AUTO_APPROVE_ENABLED');
  const canOverridePreferredSource = isUserOverridable('REQUEST_AUTO_PREFERRED_SOURCE');
  const canOverridePreferredIndexer = isUserOverridable('REQUEST_AUTO_PREFERRED_INDEXER');
  const canOverrideAutoContentTypes = isUserOverridable('REQUEST_AUTO_CONTENT_TYPES');
  const canOverrideAutoFormats = isUserOverridable('REQUEST_AUTO_FORMATS');
  const canOverrideSelectionPolicy = isUserOverridable('REQUEST_AUTO_SELECTION_POLICY');
  const canOverrideFallbackStrategy = isUserOverridable('REQUEST_AUTO_FALLBACK_STRATEGY');

  if (
    !canOverrideRequestToggle
    && !canOverrideDefaults
    && !canOverrideRules
    && !canOverrideMaxPending
    && !canOverrideAllowNotes
    && !canOverrideAutoSelect
    && !canOverrideAutoApprove
    && !canOverridePreferredSource
    && !canOverridePreferredIndexer
    && !canOverrideAutoContentTypes
    && !canOverrideAutoFormats
    && !canOverrideSelectionPolicy
    && !canOverrideFallbackStrategy
  ) {
    return null;
  }

  const showAdvancedRequestSettings = requestsEnabledValue || hasAnyAdvancedRequestOverrides;
  const showAutoSelectionSettings = (
    showAdvancedRequestSettings
    && (autoSelectEnabledValue || hasAnyAutoSelectionOverrides)
  );
  const showPreferredIndexerField = (
    canOverridePreferredIndexer
    && (preferredSourceValue === 'prowlarr' || isOverridden('REQUEST_AUTO_PREFERRED_INDEXER'))
  );

  return (
    <div className="space-y-4">
      <HeadingField field={requestPolicyHeading} />

      {canOverrideRequestToggle && (
        <FieldWrapper
          field={requestsEnabledField}
          resetAction={
            isOverridden('REQUESTS_ENABLED')
              ? {
                  disabled: Boolean(requestsEnabledField.fromEnv),
                  onClick: () => resetKeys(['REQUESTS_ENABLED']),
                }
              : undefined
          }
        >
          <CheckboxField
            field={requestsEnabledField}
            value={requestsEnabledValue}
            onChange={(value) => setOverride('REQUESTS_ENABLED', value)}
            disabled={Boolean(requestsEnabledField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {showAdvancedRequestSettings && (
        <>
          {(canOverrideDefaults || canOverrideRules) && rulesField && (
            <RequestPolicyGrid
              defaultModes={effectiveDefaults}
              onDefaultModeChange={(contentType, mode) => {
                const settingKey =
                  contentType === 'ebook'
                    ? ('REQUEST_POLICY_DEFAULT_EBOOK' as const)
                    : ('REQUEST_POLICY_DEFAULT_AUDIOBOOK' as const);
                const globalDefault = globalDefaults[contentType];

                setUserSettings((prev) => {
                  const next = { ...prev };
                  if (mode === globalDefault) {
                    delete next[settingKey];
                  } else {
                    next[settingKey] = mode;
                  }
                  return next;
                });

                const nextDefaults = {
                  ...effectiveDefaults,
                  [contentType]: mode,
                };
                setRulesOverride(explicitUserRules, nextDefaults);
              }}
              onDefaultModeReset={(contentType) => {
                const settingKey =
                  contentType === 'ebook'
                    ? ('REQUEST_POLICY_DEFAULT_EBOOK' as const)
                    : ('REQUEST_POLICY_DEFAULT_AUDIOBOOK' as const);
                setUserSettings((prev) => {
                  const next = { ...prev };
                  delete next[settingKey];
                  return next;
                });

                const nextDefaults = {
                  ...effectiveDefaults,
                  [contentType]: globalDefaults[contentType],
                };
                setRulesOverride(explicitUserRules, nextDefaults);
              }}
              defaultModeOverrides={{
                ebook: hasUserEbookDefault,
                audiobook: hasUserAudiobookDefault,
              }}
              defaultModeDisabled={{
                ebook:
                  !isUserOverridable('REQUEST_POLICY_DEFAULT_EBOOK')
                  || Boolean(defaultEbookField?.fromEnv),
                audiobook:
                  !isUserOverridable('REQUEST_POLICY_DEFAULT_AUDIOBOOK')
                  || Boolean(defaultAudioField?.fromEnv),
              }}
              explicitRules={explicitUserRules}
              baseRules={globalRules}
              onExplicitRulesChange={(rules) => setRulesOverride(rules)}
              sourceCapabilities={sourceCapabilities}
              rulesDisabled={!isUserOverridable('REQUEST_POLICY_RULES')}
              showClearOverrides
              clearOverridesDisabled={!hasAnyRequestOverrides}
              onClearOverrides={() => resetKeys(ALL_REQUEST_OVERRIDE_KEYS)}
            />
          )}

          {canOverrideMaxPending && (
            <FieldWrapper
              field={maxPendingField}
              resetAction={
                isOverridden('MAX_PENDING_REQUESTS_PER_USER')
                  ? {
                      disabled: Boolean(maxPendingField.fromEnv),
                      onClick: () => resetKeys(['MAX_PENDING_REQUESTS_PER_USER']),
                    }
                  : undefined
              }
            >
              <NumberField
                field={maxPendingField}
                value={maxPendingValue}
                onChange={(value) => setOverride('MAX_PENDING_REQUESTS_PER_USER', value)}
                disabled={Boolean(maxPendingField.fromEnv)}
              />
            </FieldWrapper>
          )}

          {canOverrideAllowNotes && (
            <FieldWrapper
              field={allowNotesField}
              resetAction={
                isOverridden('REQUESTS_ALLOW_NOTES')
                  ? {
                      disabled: Boolean(allowNotesField.fromEnv),
                      onClick: () => resetKeys(['REQUESTS_ALLOW_NOTES']),
                    }
                  : undefined
              }
            >
              <CheckboxField
                field={allowNotesField}
                value={allowNotesValue}
                onChange={(value) => setOverride('REQUESTS_ALLOW_NOTES', value)}
                disabled={Boolean(allowNotesField.fromEnv)}
              />
            </FieldWrapper>
          )}

          {(canOverrideAutoSelect
            || canOverrideAutoApprove
            || canOverridePreferredSource
            || canOverridePreferredIndexer
            || canOverrideAutoContentTypes
            || canOverrideAutoFormats
            || canOverrideSelectionPolicy
            || canOverrideFallbackStrategy) && (
            <>
              <HeadingField field={autoSelectionHeading} />

              {canOverrideAutoSelect && (
                <FieldWrapper
                  field={autoSelectField}
                  resetAction={
                    isOverridden('REQUEST_AUTO_SELECT_ENABLED')
                      ? {
                          disabled: Boolean(autoSelectField.fromEnv),
                          onClick: () => resetKeys(['REQUEST_AUTO_SELECT_ENABLED']),
                        }
                      : undefined
                  }
                >
                  <CheckboxField
                    field={autoSelectField}
                    value={autoSelectEnabledValue}
                    onChange={(value) => setOverride('REQUEST_AUTO_SELECT_ENABLED', value)}
                    disabled={Boolean(autoSelectField.fromEnv)}
                  />
                </FieldWrapper>
              )}

              {showAutoSelectionSettings && canOverrideAutoApprove && (
                <FieldWrapper
                  field={autoApproveField}
                  resetAction={
                    isOverridden('REQUEST_AUTO_APPROVE_ENABLED')
                      ? {
                          disabled: Boolean(autoApproveField.fromEnv),
                          onClick: () => resetKeys(['REQUEST_AUTO_APPROVE_ENABLED']),
                        }
                      : undefined
                  }
                >
                  <CheckboxField
                    field={autoApproveField}
                    value={autoApproveValue}
                    onChange={(value) => setOverride('REQUEST_AUTO_APPROVE_ENABLED', value)}
                    disabled={Boolean(autoApproveField.fromEnv)}
                  />
                </FieldWrapper>
              )}

              {showAutoSelectionSettings && canOverridePreferredSource && (
                <FieldWrapper
                  field={preferredSourceField}
                  resetAction={
                    isOverridden('REQUEST_AUTO_PREFERRED_SOURCE')
                      ? {
                          disabled: Boolean(preferredSourceField.fromEnv),
                          onClick: () => resetKeys(['REQUEST_AUTO_PREFERRED_SOURCE']),
                        }
                      : undefined
                  }
                >
                  <SelectField
                    field={preferredSourceField}
                    value={preferredSourceValue}
                    onChange={(value) =>
                      setUserSettings((prev) => {
                        const next = { ...prev, REQUEST_AUTO_PREFERRED_SOURCE: value };
                        if (value !== 'prowlarr') {
                          delete next.REQUEST_AUTO_PREFERRED_INDEXER;
                        }
                        return next;
                      })
                    }
                    disabled={Boolean(preferredSourceField.fromEnv)}
                  />
                </FieldWrapper>
              )}

              {showAutoSelectionSettings && showPreferredIndexerField && (
                <FieldWrapper
                  field={preferredIndexerField}
                  resetAction={
                    isOverridden('REQUEST_AUTO_PREFERRED_INDEXER')
                      ? {
                          disabled: Boolean(preferredIndexerField.fromEnv),
                          onClick: () => resetKeys(['REQUEST_AUTO_PREFERRED_INDEXER']),
                        }
                      : undefined
                  }
                >
                  <SelectField
                    field={preferredIndexerField}
                    value={preferredIndexerValue}
                    onChange={(value) => setOverride('REQUEST_AUTO_PREFERRED_INDEXER', value)}
                    disabled={Boolean(preferredIndexerField.fromEnv)}
                  />
                </FieldWrapper>
              )}

              {showAutoSelectionSettings && canOverrideAutoContentTypes && (
                <FieldWrapper
                  field={autoContentTypesField}
                  resetAction={
                    isOverridden('REQUEST_AUTO_CONTENT_TYPES')
                      ? {
                          disabled: Boolean(autoContentTypesField.fromEnv),
                          onClick: () => resetKeys(['REQUEST_AUTO_CONTENT_TYPES']),
                        }
                      : undefined
                  }
                >
                  <MultiSelectField
                    field={autoContentTypesField}
                    value={autoContentTypesValue}
                    onChange={(value) => setOverride('REQUEST_AUTO_CONTENT_TYPES', value)}
                    disabled={Boolean(autoContentTypesField.fromEnv)}
                  />
                </FieldWrapper>
              )}

              {showAutoSelectionSettings && canOverrideAutoFormats && (
                <FieldWrapper
                  field={autoFormatsField}
                  resetAction={
                    isOverridden('REQUEST_AUTO_FORMATS')
                      ? {
                          disabled: Boolean(autoFormatsField.fromEnv),
                          onClick: () => resetKeys(['REQUEST_AUTO_FORMATS']),
                        }
                      : undefined
                  }
                >
                  <MultiSelectField
                    field={autoFormatsField}
                    value={autoFormatsValue}
                    onChange={(value) => setOverride('REQUEST_AUTO_FORMATS', value)}
                    disabled={Boolean(autoFormatsField.fromEnv)}
                  />
                </FieldWrapper>
              )}

              {showAutoSelectionSettings && canOverrideSelectionPolicy && (
                <FieldWrapper
                  field={selectionPolicyField}
                  resetAction={
                    isOverridden('REQUEST_AUTO_SELECTION_POLICY')
                      ? {
                          disabled: Boolean(selectionPolicyField.fromEnv),
                          onClick: () => resetKeys(['REQUEST_AUTO_SELECTION_POLICY']),
                        }
                      : undefined
                  }
                >
                  <SelectField
                    field={selectionPolicyField}
                    value={selectionPolicyValue}
                    onChange={(value) => setOverride('REQUEST_AUTO_SELECTION_POLICY', value)}
                    disabled={Boolean(selectionPolicyField.fromEnv)}
                  />
                </FieldWrapper>
              )}

              {showAutoSelectionSettings && canOverrideFallbackStrategy && (
                <FieldWrapper
                  field={fallbackStrategyField}
                  resetAction={
                    isOverridden('REQUEST_AUTO_FALLBACK_STRATEGY')
                      ? {
                          disabled: Boolean(fallbackStrategyField.fromEnv),
                          onClick: () => resetKeys(['REQUEST_AUTO_FALLBACK_STRATEGY']),
                        }
                      : undefined
                  }
                >
                  <SelectField
                    field={fallbackStrategyField}
                    value={fallbackStrategyValue}
                    onChange={(value) => setOverride('REQUEST_AUTO_FALLBACK_STRATEGY', value)}
                    disabled={Boolean(fallbackStrategyField.fromEnv)}
                  />
                </FieldWrapper>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
};
