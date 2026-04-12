import { describe, it, expect } from 'vitest';

import type { SettingsTab } from '../types/settings';
import {
  getRestartRequiredFieldKeys,
  mergeFetchedSettingsWithDirtyValues,
  settingsTabMatchesSavedValues,
} from '../utils/settingsValues';

describe('mergeFetchedSettingsWithDirtyValues', () => {
  it('preserves unsaved dirty values while applying fresh fetched values', () => {
    const fetchedValues = {
      general: {
        apiKey: 'saved-key',
        endpoint: 'https://saved.example',
      },
    };

    const currentValues = {
      general: {
        apiKey: 'unsaved-key',
        endpoint: 'https://saved.example',
      },
    };

    const originalValues = {
      general: {
        apiKey: 'saved-key',
        endpoint: 'https://saved.example',
      },
    };

    expect(
      mergeFetchedSettingsWithDirtyValues(fetchedValues, currentValues, originalValues),
    ).toEqual({
      general: {
        apiKey: 'unsaved-key',
        endpoint: 'https://saved.example',
      },
    });
  });

  it('does not preserve values that are not dirty and ignores keys removed by backend', () => {
    const fetchedValues = {
      provider: {
        host: 'new-host',
        enabled: true,
      },
    };

    const currentValues = {
      provider: {
        host: 'old-host',
        enabled: false,
        removedField: 'local-only',
      },
    };

    const originalValues = {
      provider: {
        host: 'old-host',
        enabled: false,
        removedField: 'local-only',
      },
    };

    expect(
      mergeFetchedSettingsWithDirtyValues(fetchedValues, currentValues, originalValues),
    ).toEqual({
      provider: {
        host: 'new-host',
        enabled: true,
      },
    });
  });
});

describe('settings save verification helpers', () => {
  const tabs: SettingsTab[] = [
    {
      name: 'general',
      displayName: 'General',
      order: 1,
      fields: [
        {
          key: 'API_URL',
          label: 'API URL',
          type: 'TextField',
          value: 'https://saved.example',
        },
        {
          key: 'API_KEY',
          label: 'API Key',
          type: 'PasswordField',
          value: '',
        },
        {
          key: 'USE_SSL',
          label: 'Use SSL',
          type: 'CheckboxField',
          value: true,
          requiresRestart: true,
        },
      ],
    },
  ];

  it('confirms a saved tab when backend values match the expected non-password changes', () => {
    expect(
      settingsTabMatchesSavedValues('general', tabs, {
        API_URL: 'https://saved.example',
        API_KEY: 'secret',
      }),
    ).toBe(true);
  });

  it('does not confirm a saved tab when a non-password field does not match', () => {
    expect(
      settingsTabMatchesSavedValues('general', tabs, {
        API_URL: 'https://different.example',
      }),
    ).toBe(false);
  });

  it('collects restart-required keys for changed values', () => {
    expect(
      getRestartRequiredFieldKeys(tabs[0].fields, {
        API_URL: 'https://saved.example',
        USE_SSL: true,
      }),
    ).toEqual(['USE_SSL']);
  });
});
