import { DeliveryPreferencesResponse } from '../../../services/api';
import { HeadingFieldConfig, SelectFieldConfig } from '../../../types/settings';
import { HeadingField, SelectField } from '../fields';
import { FieldWrapper } from '../shared';
import { getFieldByKey } from './fieldHelpers';
import { PerUserSettings } from './types';

interface UserSearchPreferencesSectionProps {
  searchPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
}

type SearchSettingKey =
  | 'SEARCH_MODE'
  | 'METADATA_PROVIDER'
  | 'METADATA_PROVIDER_AUDIOBOOK'
  | 'DEFAULT_RELEASE_SOURCE';

const fallbackSearchModeField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'SEARCH_MODE',
  label: 'Search Mode',
  description: 'How you want to search for and download books.',
  value: 'direct',
  options: [
    { value: 'direct', label: 'Direct' },
    { value: 'universal', label: 'Universal' },
  ],
};

const fallbackMetadataProviderField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'METADATA_PROVIDER',
  label: 'Book Metadata Provider',
  description: 'Choose which metadata provider to use for book searches.',
  value: '',
  options: [],
};

const fallbackAudiobookMetadataProviderField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'METADATA_PROVIDER_AUDIOBOOK',
  label: 'Audiobook Metadata Provider',
  description: 'Metadata provider for audiobook searches. Uses the book provider if not set.',
  value: '',
  options: [{ value: '', label: 'Use main provider' }],
};

const fallbackDefaultReleaseSourceField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'DEFAULT_RELEASE_SOURCE',
  label: 'Default Release Source',
  description: 'The release source tab to open by default in the release modal.',
  value: 'direct_download',
  options: [],
};

const searchHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'search_preferences_heading',
  title: 'Search Preferences',
  description: 'Personal search settings for this user. Reset to inherit global defaults from Search Mode.',
};

const normalizeSearchMode = (value: unknown): 'direct' | 'universal' => {
  const normalized = String(value || '').trim().toLowerCase();
  return normalized === 'universal' ? 'universal' : 'direct';
};

const toStringValue = (value: unknown): string => {
  if (value === undefined || value === null) {
    return '';
  }
  return String(value);
};

export const UserSearchPreferencesSection = ({
  searchPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
}: UserSearchPreferencesSectionProps) => {
  if (!searchPreferences) {
    return null;
  }

  const fields = searchPreferences.fields ?? [];
  const globalValues = searchPreferences.globalValues ?? {};
  const preferenceKeySet = new Set(searchPreferences.keys ?? []);

  const searchModeField = getFieldByKey<SelectFieldConfig>(
    fields,
    'SEARCH_MODE',
    fallbackSearchModeField
  );
  const metadataProviderField = getFieldByKey<SelectFieldConfig>(
    fields,
    'METADATA_PROVIDER',
    fallbackMetadataProviderField
  );
  const metadataProviderAudiobookField = getFieldByKey<SelectFieldConfig>(
    fields,
    'METADATA_PROVIDER_AUDIOBOOK',
    fallbackAudiobookMetadataProviderField
  );
  const defaultReleaseSourceField = getFieldByKey<SelectFieldConfig>(
    fields,
    'DEFAULT_RELEASE_SOURCE',
    fallbackDefaultReleaseSourceField
  );

  const isOverridden = (key: SearchSettingKey): boolean => {
    if (
      !Object.prototype.hasOwnProperty.call(userSettings, key)
      || userSettings[key] === null
      || userSettings[key] === undefined
    ) {
      return false;
    }

    return toStringValue(userSettings[key]) !== toStringValue(globalValues[key]);
  };

  const readValue = (key: SearchSettingKey, fallback = ''): string => {
    if (isOverridden(key)) {
      return toStringValue(userSettings[key]);
    }
    if (Object.prototype.hasOwnProperty.call(globalValues, key)) {
      return toStringValue(globalValues[key]);
    }
    return fallback;
  };

  const resetKeys = (keys: SearchSettingKey[]) => {
    setUserSettings((prev) => {
      const next = { ...prev };
      keys.forEach((key) => {
        delete next[key];
      });
      return next;
    });
  };

  const searchModeValue = readValue('SEARCH_MODE', 'direct');
  const effectiveSearchMode = normalizeSearchMode(searchModeValue);
  const metadataProviderValue = readValue('METADATA_PROVIDER');
  const metadataProviderAudiobookValue = readValue('METADATA_PROVIDER_AUDIOBOOK');
  const defaultReleaseSourceValue = readValue('DEFAULT_RELEASE_SOURCE', 'direct_download');

  const canOverrideSearchMode = isUserOverridable('SEARCH_MODE') && preferenceKeySet.has('SEARCH_MODE');
  const canOverrideMetadataProvider = isUserOverridable('METADATA_PROVIDER')
    && preferenceKeySet.has('METADATA_PROVIDER');
  const canOverrideAudiobookMetadataProvider = isUserOverridable('METADATA_PROVIDER_AUDIOBOOK')
    && preferenceKeySet.has('METADATA_PROVIDER_AUDIOBOOK');
  const canOverrideDefaultReleaseSource = isUserOverridable('DEFAULT_RELEASE_SOURCE')
    && preferenceKeySet.has('DEFAULT_RELEASE_SOURCE');

  if (
    !canOverrideSearchMode
    && !canOverrideMetadataProvider
    && !canOverrideAudiobookMetadataProvider
    && !canOverrideDefaultReleaseSource
  ) {
    return null;
  }

  return (
    <div className="space-y-4">
      <HeadingField field={searchHeading} />

      {canOverrideSearchMode && (
        <FieldWrapper
          field={searchModeField}
          resetAction={
            isOverridden('SEARCH_MODE')
              ? {
                  disabled: Boolean(searchModeField.fromEnv),
                  onClick: () => resetKeys(['SEARCH_MODE']),
                }
              : undefined
          }
        >
          <SelectField
            field={searchModeField}
            value={searchModeValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, SEARCH_MODE: value }))}
            disabled={Boolean(searchModeField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveSearchMode === 'universal' && canOverrideMetadataProvider && (
        <FieldWrapper
          field={metadataProviderField}
          resetAction={
            isOverridden('METADATA_PROVIDER')
              ? {
                  disabled: Boolean(metadataProviderField.fromEnv),
                  onClick: () => resetKeys(['METADATA_PROVIDER']),
                }
              : undefined
          }
        >
          <SelectField
            field={metadataProviderField}
            value={metadataProviderValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, METADATA_PROVIDER: value }))}
            disabled={Boolean(metadataProviderField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveSearchMode === 'universal' && canOverrideAudiobookMetadataProvider && (
        <FieldWrapper
          field={metadataProviderAudiobookField}
          resetAction={
            isOverridden('METADATA_PROVIDER_AUDIOBOOK')
              ? {
                  disabled: Boolean(metadataProviderAudiobookField.fromEnv),
                  onClick: () => resetKeys(['METADATA_PROVIDER_AUDIOBOOK']),
                }
              : undefined
          }
        >
          <SelectField
            field={metadataProviderAudiobookField}
            value={metadataProviderAudiobookValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, METADATA_PROVIDER_AUDIOBOOK: value }))}
            disabled={Boolean(metadataProviderAudiobookField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveSearchMode === 'universal' && canOverrideDefaultReleaseSource && (
        <FieldWrapper
          field={defaultReleaseSourceField}
          resetAction={
            isOverridden('DEFAULT_RELEASE_SOURCE')
              ? {
                  disabled: Boolean(defaultReleaseSourceField.fromEnv),
                  onClick: () => resetKeys(['DEFAULT_RELEASE_SOURCE']),
                }
              : undefined
          }
        >
          <SelectField
            field={defaultReleaseSourceField}
            value={defaultReleaseSourceValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, DEFAULT_RELEASE_SOURCE: value }))}
            disabled={Boolean(defaultReleaseSourceField.fromEnv)}
          />
        </FieldWrapper>
      )}
    </div>
  );
};
