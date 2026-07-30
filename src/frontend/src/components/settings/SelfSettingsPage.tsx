import { useCallback, useState } from 'react';

import { useMountEffect } from '../../hooks/useMountEffect';
import { getSelfSettings, testPersonalNotification, updateSelfSettings } from '../../services/api';
import type { CheckboxFieldConfig, SelectFieldConfig, TextFieldConfig } from '../../types/settings';
import {
  getStoredThemePreference,
  setThemePreference,
  THEME_FIELD,
} from '../../utils/themePreference';
import { CheckboxField, SelectField, TextField } from './fields';
import { FieldWrapper } from './shared';

interface SelfSettingsPageProps {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved?: () => void;
}

interface SelfSettingsForm {
  username: string;
  email: string;
  display_name: string;
  kindle_address: string;
  notifications_enabled: boolean;
  notification_transport: 'email' | 'apprise';
  notification_destination: string;
}

const readOnlyField = (key: string, label: string, value: string): TextFieldConfig => ({
  type: 'TextField',
  key,
  label,
  value,
  disabled: true,
});

const textField = (
  key: string,
  label: string,
  value: string,
  description?: string,
): TextFieldConfig => ({
  type: 'TextField',
  key,
  label,
  value,
  description,
});

const notificationEnabledField: CheckboxFieldConfig = {
  type: 'CheckboxField',
  key: 'notifications_enabled',
  label: 'Enable personal notifications',
  description: 'Notifications are delivered only to your selected personal destination.',
  value: false,
};

const notificationTransportField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'notification_transport',
  label: 'Notification transport',
  options: [
    { value: 'email', label: 'Email' },
    { value: 'apprise', label: 'Apprise' },
  ],
  value: 'email',
};

export const SelfSettingsPage = ({ onShowToast, onSettingsSaved }: SelfSettingsPageProps) => {
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isTestingNotification, setIsTestingNotification] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [values, setValues] = useState<SelfSettingsForm>({
    username: '',
    email: '',
    display_name: '',
    kindle_address: '',
    notifications_enabled: false,
    notification_transport: 'email',
    notification_destination: '',
  });
  const [originalValues, setOriginalValues] = useState<SelfSettingsForm>(values);
  const [themeValue, setThemeValue] = useState(getStoredThemePreference());

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const settings = await getSelfSettings();
      const next: SelfSettingsForm = {
        username: settings.username,
        email: settings.email || '',
        display_name: settings.display_name || '',
        kindle_address: settings.kindle_address || '',
        notifications_enabled: settings.notifications_enabled,
        notification_transport: settings.notification_transport === 'apprise' ? 'apprise' : 'email',
        notification_destination: settings.notification_destination || '',
      };
      setValues(next);
      setOriginalValues(next);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load settings');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useMountEffect(() => {
    void load();
  });
  const hasChanges = JSON.stringify(values) !== JSON.stringify(originalValues);
  const update = (field: keyof typeof values, value: string | boolean) =>
    setValues((current) => ({ ...current, [field]: value }));
  const save = async () => {
    if (!hasChanges) return;
    setIsSaving(true);
    try {
      const saved = await updateSelfSettings({
        display_name: values.display_name || null,
        kindle_address: values.kindle_address || null,
        notifications_enabled: values.notifications_enabled,
        notification_transport: values.notification_transport,
        notification_destination: values.notification_destination || null,
      });
      setOriginalValues({
        ...values,
        display_name: saved.display_name || '',
        kindle_address: saved.kindle_address || '',
      });
      onShowToast?.('Settings updated', 'success');
      onSettingsSaved?.();
    } catch (saveError) {
      onShowToast?.(
        saveError instanceof Error ? saveError.message : 'Failed to update settings',
        'error',
      );
    } finally {
      setIsSaving(false);
    }
  };
  const testNotification = async () => {
    setIsTestingNotification(true);
    try {
      const result = await testPersonalNotification();
      onShowToast?.(result.message, result.success ? 'success' : 'error');
    } catch (testError) {
      onShowToast?.(
        testError instanceof Error ? testError.message : 'Failed to send test notification',
        'error',
      );
    } finally {
      setIsTestingNotification(false);
    }
  };

  return (
    <section className="mx-auto max-w-2xl space-y-5">
      <header className="border-b border-(--border-muted) pb-4">
        <h2 className="text-lg font-semibold">Personal settings</h2>
      </header>
      <div className="space-y-5">
        {isLoading && <p className="text-sm opacity-60">Loading settings...</p>}
        {!isLoading && error && (
          <div className="space-y-3">
            <p className="text-sm text-red-600">{error}</p>
            <button type="button" onClick={() => void load()} className="text-sm underline">
              Retry
            </button>
          </div>
        )}
        {!isLoading && !error && (
          <>
            <section className="space-y-4">
              <h4 className="text-sm font-medium">Account</h4>
              <FieldWrapper field={readOnlyField('username', 'Username', values.username)}>
                <TextField
                  field={readOnlyField('username', 'Username', values.username)}
                  value={values.username}
                  onChange={() => undefined}
                  disabled
                />
              </FieldWrapper>
              <FieldWrapper field={readOnlyField('email', 'Account email', values.email)}>
                <TextField
                  field={readOnlyField('email', 'Account email', values.email)}
                  value={values.email}
                  onChange={() => undefined}
                  disabled
                />
              </FieldWrapper>
              <FieldWrapper field={textField('display_name', 'Display name', values.display_name)}>
                <TextField
                  field={textField('display_name', 'Display name', values.display_name)}
                  value={values.display_name}
                  onChange={(value) => update('display_name', value)}
                />
              </FieldWrapper>
            </section>
            <section className="space-y-4 border-t border-(--border-muted) pt-5">
              <h4 className="text-sm font-medium">Delivery</h4>
              <FieldWrapper
                field={textField(
                  'kindle_address',
                  'Send-to-Kindle recipient',
                  values.kindle_address,
                  'Used only for Send to Kindle. Any email recipient address is allowed.',
                )}
              >
                <TextField
                  field={textField(
                    'kindle_address',
                    'Send-to-Kindle recipient',
                    values.kindle_address,
                  )}
                  value={values.kindle_address}
                  onChange={(value) => update('kindle_address', value)}
                />
              </FieldWrapper>
            </section>
            <section className="space-y-4 border-t border-(--border-muted) pt-5">
              <h4 className="text-sm font-medium">Personal Notifications</h4>
              <FieldWrapper field={notificationEnabledField}>
                <CheckboxField
                  field={notificationEnabledField}
                  value={values.notifications_enabled}
                  onChange={(value) => update('notifications_enabled', value)}
                />
              </FieldWrapper>
              <FieldWrapper field={notificationTransportField}>
                <SelectField
                  field={notificationTransportField}
                  value={values.notification_transport}
                  onChange={(value) =>
                    setValues((current) => ({
                      ...current,
                      notification_transport: value === 'apprise' ? 'apprise' : 'email',
                      notification_destination: '',
                    }))
                  }
                />
              </FieldWrapper>
              <button
                type="button"
                onClick={() => void testNotification()}
                disabled={!values.notifications_enabled || hasChanges || isTestingNotification}
                className="rounded-lg border border-(--border-muted) px-3 py-2 text-sm disabled:opacity-60"
              >
                {isTestingNotification ? 'Sending test...' : 'Test saved destination'}
              </button>
              <FieldWrapper
                field={textField(
                  'notification_destination',
                  values.notification_transport === 'email' ? 'Email address' : 'Apprise URL',
                  values.notification_destination,
                )}
              >
                <TextField
                  field={textField(
                    'notification_destination',
                    values.notification_transport === 'email' ? 'Email address' : 'Apprise URL',
                    values.notification_destination,
                  )}
                  value={values.notification_destination}
                  onChange={(value) => update('notification_destination', value)}
                />
              </FieldWrapper>
            </section>
            <section className="border-t border-(--border-muted) pt-5">
              <FieldWrapper field={THEME_FIELD}>
                <SelectField
                  field={THEME_FIELD}
                  value={themeValue}
                  onChange={(value) => {
                    setThemeValue(value);
                    setThemePreference(value);
                  }}
                />
              </FieldWrapper>
            </section>
          </>
        )}
      </div>
      <footer className="flex justify-end gap-3 border-t border-(--border-muted) pt-4">
        <button
          type="button"
          onClick={() => void load()}
          disabled={isSaving}
          className="rounded-lg border border-(--border-muted) px-4 py-2 text-sm"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void save()}
          disabled={!hasChanges || isSaving || isLoading}
          className="rounded-lg bg-sky-600 px-4 py-2 text-sm text-white disabled:opacity-60"
        >
          {isSaving ? 'Saving...' : 'Save Changes'}
        </button>
      </footer>
    </section>
  );
};
