import { DeliveryPreferencesResponse } from '../../../services/api';
import {
  HeadingFieldConfig,
  SelectFieldConfig,
  SettingsField,
  TextFieldConfig,
} from '../../../types/settings';
import { HeadingField, SelectField, TextField } from '../fields';
import { FieldWrapper } from '../shared';
import { PerUserSettings } from './types';

interface UserOverridesSectionProps {
  deliveryPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
}

const modeOptions = [
  { value: 'folder', label: 'Folder' },
  { value: 'email', label: 'Email (SMTP)' },
  { value: 'booklore', label: 'BookLore (API)' },
];

const fallbackOutputModeField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'BOOKS_OUTPUT_MODE',
  label: 'Output Mode',
  description: 'Choose where completed book files are sent.',
  value: 'folder',
  options: modeOptions,
};

const fallbackDestinationField: TextFieldConfig = {
  type: 'TextField',
  key: 'DESTINATION',
  label: 'Destination',
  description: 'Directory where downloaded files are saved.',
  value: '',
  placeholder: '/books',
};

const fallbackDestinationAudiobookField: TextFieldConfig = {
  type: 'TextField',
  key: 'DESTINATION_AUDIOBOOK',
  label: 'Destination',
  description: 'Directory for this user\'s audiobook downloads.',
  value: '',
};

const fallbackBookloreLibraryField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'BOOKLORE_LIBRARY_ID',
  label: 'Library',
  description: 'BookLore library to upload into.',
  value: '',
  options: [],
};

const fallbackBooklorePathField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'BOOKLORE_PATH_ID',
  label: 'Path',
  description: 'BookLore library path for uploads.',
  value: '',
  options: [],
  filterByField: 'BOOKLORE_LIBRARY_ID',
};

const fallbackEmailRecipientField: TextFieldConfig = {
  type: 'TextField',
  key: 'EMAIL_RECIPIENT',
  label: 'Email Recipient',
  description: 'Email address used for this user in Email output mode.',
  value: '',
  placeholder: 'reader@example.com',
};

type DeliverySettingKey = keyof PerUserSettings;

function normalizeMode(value: unknown): 'folder' | 'booklore' | 'email' {
  const mode = String(value || '').trim().toLowerCase();
  if (mode === 'booklore' || mode === 'email') {
    return mode;
  }
  return 'folder';
}

function toStringValue(value: unknown): string {
  if (value === undefined || value === null) return '';
  return String(value);
}

function getFieldByKey<T extends SettingsField>(
  fields: SettingsField[] | undefined,
  key: string,
  fallback: T
): T {
  const found = fields?.find((field) => field.key === key);
  if (!found) {
    return fallback;
  }
  return found as T;
}

interface ResetOverrideButtonProps {
  disabled?: boolean;
  label?: string;
  onClick: () => void;
}

const ResetOverrideButton = ({ disabled = false, label = 'Reset', onClick }: ResetOverrideButtonProps) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className="px-2.5 py-1 rounded-lg text-xs font-medium border border-[var(--border-muted)]
               bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors
               disabled:opacity-50 disabled:cursor-not-allowed"
  >
    {label}
  </button>
);

const deliveryHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'delivery_preferences_heading',
  title: 'Delivery Preferences',
  description: 'Editing values here creates per-user settings. Use Reset to inherit global values.',
};

const booksHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'delivery_preferences_books_heading',
  title: 'Books',
  description: 'Output mode and destination behavior for ebooks, comics, and magazines.',
};

const audiobooksHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'delivery_preferences_audiobooks_heading',
  title: 'Audiobooks',
  description: 'Audiobooks always use folder output. Use Reset to inherit the global audiobook destination.',
};

const BOOK_PREFERENCE_KEYS: DeliverySettingKey[] = [
  'BOOKS_OUTPUT_MODE',
  'DESTINATION',
  'BOOKLORE_LIBRARY_ID',
  'BOOKLORE_PATH_ID',
  'EMAIL_RECIPIENT',
];

const AUDIOBOOK_PREFERENCE_KEYS: DeliverySettingKey[] = ['DESTINATION_AUDIOBOOK'];

export const UserOverridesSection = ({
  deliveryPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
}: UserOverridesSectionProps) => {
  const fields = deliveryPreferences?.fields ?? [];
  const globalValues = deliveryPreferences?.globalValues ?? {};
  const preferenceKeys = deliveryPreferences?.keys ?? [];

  const outputModeField = getFieldByKey<SelectFieldConfig>(fields, 'BOOKS_OUTPUT_MODE', fallbackOutputModeField);
  const destinationField = getFieldByKey<TextFieldConfig>(fields, 'DESTINATION', fallbackDestinationField);
  const destinationAudiobookField = getFieldByKey<TextFieldConfig>(fields, 'DESTINATION_AUDIOBOOK', fallbackDestinationAudiobookField);
  const bookloreLibraryField = getFieldByKey<SelectFieldConfig>(fields, 'BOOKLORE_LIBRARY_ID', fallbackBookloreLibraryField);
  const booklorePathField = getFieldByKey<SelectFieldConfig>(fields, 'BOOKLORE_PATH_ID', fallbackBooklorePathField);
  const emailRecipientFieldSource = getFieldByKey<TextFieldConfig>(fields, 'EMAIL_RECIPIENT', fallbackEmailRecipientField);
  const emailRecipientField: TextFieldConfig = {
    ...emailRecipientFieldSource,
    label: 'Email Recipient',
    description: 'Email address used for this user in Email output mode.',
  };

  const isOverridden = (key: DeliverySettingKey): boolean => {
    if (
      !Object.prototype.hasOwnProperty.call(userSettings, key) ||
      userSettings[key] === null ||
      userSettings[key] === undefined
    ) {
      return false;
    }

    const userValue = toStringValue(userSettings[key]);
    const globalValue = toStringValue(globalValues[key as string]);
    return userValue !== globalValue;
  };

  const resetKeys = (keys: DeliverySettingKey[]) => {
    setUserSettings((prev) => {
      const next = { ...prev };
      keys.forEach((key) => {
        delete next[key];
      });
      return next;
    });
  };

  const readValue = (key: DeliverySettingKey, fallback = ''): string => {
    if (isOverridden(key)) {
      return toStringValue(userSettings[key]);
    }
    if (key in globalValues) {
      return toStringValue(globalValues[key]);
    }
    return fallback;
  };

  const outputModeValue = readValue('BOOKS_OUTPUT_MODE', 'folder');
  const effectiveOutputMode = normalizeMode(outputModeValue);

  const destinationValue = readValue('DESTINATION');
  const destinationAudiobookValue = readValue('DESTINATION_AUDIOBOOK');
  const libraryValue = readValue('BOOKLORE_LIBRARY_ID');
  const pathValue = readValue('BOOKLORE_PATH_ID');
  const emailRecipientValue = readValue('EMAIL_RECIPIENT');

  const availableBookPreferenceKeys = BOOK_PREFERENCE_KEYS.filter((key) => preferenceKeys.includes(String(key)));
  const availableAudiobookPreferenceKeys = AUDIOBOOK_PREFERENCE_KEYS.filter((key) => preferenceKeys.includes(String(key)));

  const hasBookDeliveryOverride = availableBookPreferenceKeys.some((key) => isOverridden(key));
  const hasAudiobookDeliveryOverride = availableAudiobookPreferenceKeys.some((key) => isOverridden(key));

  const canOverrideOutputMode = isUserOverridable('BOOKS_OUTPUT_MODE');
  const canOverrideDestination = isUserOverridable('DESTINATION');
  const canOverrideAudiobookDestination = isUserOverridable('DESTINATION_AUDIOBOOK');
  const canOverrideBookloreLibrary = isUserOverridable('BOOKLORE_LIBRARY_ID');
  const canOverrideBooklorePath = isUserOverridable('BOOKLORE_PATH_ID');
  const canOverrideEmailRecipient = isUserOverridable('EMAIL_RECIPIENT');

  if (!deliveryPreferences) {
    return null;
  }

  return (
    <div className="space-y-4">
      <HeadingField field={deliveryHeading} />
      <HeadingField field={booksHeading} />

      {canOverrideOutputMode && (
        <FieldWrapper
          field={outputModeField}
          headerRight={
            hasBookDeliveryOverride ? (
              <ResetOverrideButton
                label="Reset all"
                onClick={() => resetKeys(availableBookPreferenceKeys)}
              />
            ) : undefined
          }
        >
          <SelectField
            field={outputModeField}
            value={outputModeValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, BOOKS_OUTPUT_MODE: value }))}
            disabled={Boolean(outputModeField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveOutputMode === 'folder' && canOverrideDestination && (
        <FieldWrapper
          field={destinationField}
          headerRight={
            isOverridden('DESTINATION') ? (
              <ResetOverrideButton
                disabled={Boolean(destinationField.fromEnv)}
                onClick={() => resetKeys(['DESTINATION'])}
              />
            ) : undefined
          }
        >
          <TextField
            field={destinationField}
            value={destinationValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, DESTINATION: value }))}
            disabled={Boolean(destinationField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveOutputMode === 'booklore' && canOverrideBookloreLibrary && (
        <FieldWrapper
          field={bookloreLibraryField}
          headerRight={
            isOverridden('BOOKLORE_LIBRARY_ID') ? (
              <ResetOverrideButton
                disabled={Boolean(bookloreLibraryField.fromEnv)}
                onClick={() => resetKeys(['BOOKLORE_LIBRARY_ID'])}
              />
            ) : undefined
          }
        >
          <SelectField
            field={bookloreLibraryField}
            value={libraryValue}
            onChange={(value) => {
              setUserSettings((prev) => ({
                ...prev,
                BOOKLORE_LIBRARY_ID: value,
                BOOKLORE_PATH_ID: '',
              }));
            }}
            disabled={Boolean(bookloreLibraryField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveOutputMode === 'booklore' && canOverrideBooklorePath && (
        <FieldWrapper
          field={booklorePathField}
          headerRight={
            isOverridden('BOOKLORE_PATH_ID') ? (
              <ResetOverrideButton
                disabled={Boolean(booklorePathField.fromEnv)}
                onClick={() => resetKeys(['BOOKLORE_PATH_ID'])}
              />
            ) : undefined
          }
        >
          <SelectField
            field={booklorePathField}
            value={pathValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, BOOKLORE_PATH_ID: value }))}
            disabled={Boolean(booklorePathField.fromEnv)}
            filterValue={libraryValue || undefined}
          />
        </FieldWrapper>
      )}

      {effectiveOutputMode === 'email' && canOverrideEmailRecipient && (
        <FieldWrapper
          field={emailRecipientField}
          headerRight={
            isOverridden('EMAIL_RECIPIENT') ? (
              <ResetOverrideButton
                disabled={Boolean(emailRecipientField.fromEnv)}
                onClick={() => resetKeys(['EMAIL_RECIPIENT'])}
              />
            ) : undefined
          }
        >
          <TextField
            field={emailRecipientField}
            value={emailRecipientValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, EMAIL_RECIPIENT: value }))}
            disabled={Boolean(emailRecipientField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {canOverrideAudiobookDestination && (
        <>
          <HeadingField field={audiobooksHeading} />
          <FieldWrapper
            field={destinationAudiobookField}
            headerRight={
              hasAudiobookDeliveryOverride ? (
                <ResetOverrideButton
                  disabled={Boolean(destinationAudiobookField.fromEnv)}
                  onClick={() => resetKeys(availableAudiobookPreferenceKeys)}
                />
              ) : undefined
            }
          >
          <TextField
            field={destinationAudiobookField}
            value={destinationAudiobookValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, DESTINATION_AUDIOBOOK: value }))}
            disabled={Boolean(destinationAudiobookField.fromEnv)}
          />
          </FieldWrapper>
        </>
      )}
    </div>
  );
};
