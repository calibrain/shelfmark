import { DeliveryPreferencesResponse } from '../../../services/api';
import {
  CheckboxFieldConfig,
  HeadingFieldConfig,
  SettingsField,
  TableFieldConfig,
} from '../../../types/settings';
import { CheckboxField, HeadingField, TableField } from '../fields';
import { FieldWrapper } from '../shared';
import { PerUserSettings } from './types';

interface UserNotificationOverridesSectionProps {
  notificationPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
}

type NotificationSettingKey =
  | 'USER_NOTIFICATIONS_ENABLED'
  | 'USER_NOTIFICATION_ROUTES';

const ROUTE_EVENT_ALL = 'all';
const ROUTE_EVENT_OPTIONS = [
  { value: ROUTE_EVENT_ALL, label: 'All' },
  { value: 'request_created', label: 'New request submitted' },
  { value: 'request_fulfilled', label: 'Request fulfilled' },
  { value: 'request_rejected', label: 'Request rejected' },
  { value: 'download_complete', label: 'Download complete' },
  { value: 'download_failed', label: 'Download failed' },
];
const ALLOWED_ROUTE_EVENTS = new Set(ROUTE_EVENT_OPTIONS.map((option) => option.value));

const fallbackEnabledField: CheckboxFieldConfig = {
  type: 'CheckboxField',
  key: 'USER_NOTIFICATIONS_ENABLED',
  label: 'Enable Notifications',
  description: 'Receive personal notifications for your own requests and downloads.',
  value: false,
};

const fallbackRoutesField: TableFieldConfig = {
  type: 'TableField',
  key: 'USER_NOTIFICATION_ROUTES',
  label: '',
  description: (
    'Create one route per URL. Start with All, then add event-specific routes '
    + 'for targeted delivery. Need format examples? '
    + '[View Apprise URL formats](https://appriseit.com/services/).'
  ),
  value: [{ event: ROUTE_EVENT_ALL, url: '' }],
  columns: [
    {
      key: 'event',
      label: 'Event',
      type: 'select',
      options: ROUTE_EVENT_OPTIONS,
      defaultValue: ROUTE_EVENT_ALL,
    },
    {
      key: 'url',
      label: 'Notification URL',
      type: 'text',
      placeholder: 'e.g. ntfys://ntfy.sh/username-topic',
    },
  ],
  addLabel: 'Add Route',
  emptyMessage: 'No routes configured.',
};

const notificationHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'notification_preferences_heading',
  title: 'Notifications',
  description: (
    'Personal notification preferences for this user. '
    + 'Reset any value to inherit global defaults from the Notifications tab.'
  ),
};

interface ResetOverrideButtonProps {
  disabled?: boolean;
  label?: string;
  onClick: () => void;
}

const ResetOverrideButton = ({
  disabled = false,
  label = 'Reset',
  onClick,
}: ResetOverrideButtonProps) => (
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

function normalizeComparableValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function normalizeRoutesValue(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [{ event: ROUTE_EVENT_ALL, url: '' }];
  }

  const normalized: Array<Record<string, unknown>> = [];
  const seen = new Set<string>();

  value.forEach((row) => {
    if (!row || typeof row !== 'object') {
      return;
    }

    const eventRaw = (row as Record<string, unknown>).event;
    const event = String(eventRaw ?? '').trim().toLowerCase();
    if (!ALLOWED_ROUTE_EVENTS.has(event)) {
      return;
    }

    const url = String((row as Record<string, unknown>).url ?? '').trim();
    const key = `${event}::${url}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);

    normalized.push({ event, url });
  });

  return normalized.length > 0 ? normalized : [{ event: ROUTE_EVENT_ALL, url: '' }];
}

function toBoolean(value: unknown, fallback = false): boolean {
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
  if (value === null || value === undefined) {
    return fallback;
  }
  return Boolean(value);
}

export const UserNotificationOverridesSection = ({
  notificationPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
}: UserNotificationOverridesSectionProps) => {
  if (!notificationPreferences) {
    return null;
  }

  const fields = notificationPreferences.fields ?? [];
  const globalValues = notificationPreferences.globalValues ?? {};
  const preferenceKeys = notificationPreferences.keys ?? [];

  const enabledField = getFieldByKey<CheckboxFieldConfig>(
    fields,
    'USER_NOTIFICATIONS_ENABLED',
    fallbackEnabledField
  );
  const routesField = getFieldByKey<TableFieldConfig>(
    fields,
    'USER_NOTIFICATION_ROUTES',
    fallbackRoutesField
  );

  const isOverridden = (key: NotificationSettingKey): boolean => {
    if (
      !Object.prototype.hasOwnProperty.call(userSettings, key)
      || userSettings[key] === null
      || userSettings[key] === undefined
    ) {
      return false;
    }

    if (key === 'USER_NOTIFICATION_ROUTES') {
      return JSON.stringify(normalizeRoutesValue(userSettings[key]))
        !== JSON.stringify(normalizeRoutesValue(globalValues[key]));
    }

    return normalizeComparableValue(userSettings[key])
      !== normalizeComparableValue(globalValues[key]);
  };

  const resetKeys = (keys: NotificationSettingKey[]) => {
    setUserSettings((prev) => {
      const next = { ...prev };
      keys.forEach((key) => {
        delete next[key];
      });
      return next;
    });
  };

  const readBooleanValue = (key: NotificationSettingKey, fallback = false): boolean => {
    if (isOverridden(key)) {
      return toBoolean(userSettings[key], fallback);
    }
    if (Object.prototype.hasOwnProperty.call(globalValues, key)) {
      return toBoolean(globalValues[key], fallback);
    }
    return fallback;
  };

  const readRoutesValue = (key: NotificationSettingKey): Array<Record<string, unknown>> => {
    if (isOverridden(key)) {
      return normalizeRoutesValue(userSettings[key]);
    }
    if (Object.prototype.hasOwnProperty.call(globalValues, key)) {
      return normalizeRoutesValue(globalValues[key]);
    }
    return normalizeRoutesValue([]);
  };

  const enabled = readBooleanValue('USER_NOTIFICATIONS_ENABLED', false);
  const routesValue = readRoutesValue('USER_NOTIFICATION_ROUTES');

  const notificationKeys: NotificationSettingKey[] = [
    'USER_NOTIFICATIONS_ENABLED',
    'USER_NOTIFICATION_ROUTES',
  ];

  const availableNotificationKeys = notificationKeys.filter((key) => preferenceKeys.includes(key));
  const hasNotificationOverrides = availableNotificationKeys.some((key) => isOverridden(key));

  const canOverrideEnabled = isUserOverridable('USER_NOTIFICATIONS_ENABLED');
  const canOverrideRoutes = isUserOverridable('USER_NOTIFICATION_ROUTES');

  if (!canOverrideEnabled && !canOverrideRoutes) {
    return null;
  }

  return (
    <div className="space-y-4">
      <HeadingField field={notificationHeading} />

      {canOverrideEnabled && (
        <FieldWrapper
          field={enabledField}
          headerRight={
            hasNotificationOverrides ? (
              <ResetOverrideButton
                label="Reset all"
                onClick={() => resetKeys(availableNotificationKeys)}
              />
            ) : undefined
          }
        >
          <CheckboxField
            field={enabledField}
            value={enabled}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, USER_NOTIFICATIONS_ENABLED: value }))}
            disabled={Boolean(enabledField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {enabled && canOverrideRoutes && (
        <FieldWrapper
          field={routesField}
          headerRight={
            isOverridden('USER_NOTIFICATION_ROUTES') ? (
              <ResetOverrideButton
                disabled={Boolean(routesField.fromEnv)}
                onClick={() => resetKeys(['USER_NOTIFICATION_ROUTES'])}
              />
            ) : undefined
          }
        >
          <TableField
            field={routesField}
            value={routesValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, USER_NOTIFICATION_ROUTES: value }))}
            disabled={Boolean(routesField.fromEnv)}
          />
        </FieldWrapper>
      )}
    </div>
  );
};
