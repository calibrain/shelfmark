import { describe, it, expect } from 'vitest';

import type { SettingsTab } from '../types/settings';
import {
  getRestartRequiredFieldKeys,
  mergeFetchedSettingsWithDirtyValues,
  normalizeDependentSelectValues,
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

describe('normalizeDependentSelectValues', () => {
  it('clears an invalid child select when its parent changes', () => {
    const fields: SettingsTab['fields'] = [
      {
        key: 'OUTPUT_MODE',
        label: 'Output Mode',
        type: 'SelectField',
        value: 'folder',
        options: [
          { value: 'folder', label: 'Folder' },
          { value: 'email', label: 'Email' },
        ],
      },
      {
        key: 'EMAIL_FORMAT',
        label: 'Email Format',
        type: 'SelectField',
        value: 'epub',
        filterByField: 'OUTPUT_MODE',
        options: [
          { value: 'epub', label: 'EPUB', childOf: 'email' },
          { value: 'pdf', label: 'PDF', childOf: 'email' },
        ],
      },
    ];

    expect(
      normalizeDependentSelectValues(fields, {
        OUTPUT_MODE: 'folder',
        EMAIL_FORMAT: 'epub',
      }),
    ).toEqual({
      OUTPUT_MODE: 'folder',
      EMAIL_FORMAT: '',
    });
  });

  it('preserves a dependent select when it remains valid for the parent', () => {
    const fields: SettingsTab['fields'] = [
      {
        key: 'LIBRARY_ID',
        label: 'Library',
        type: 'SelectField',
        value: '1',
        options: [
          { value: '1', label: 'Main' },
          { value: '2', label: 'Audio' },
        ],
      },
      {
        key: 'PATH_ID',
        label: 'Path',
        type: 'SelectField',
        value: '10',
        filterByField: 'LIBRARY_ID',
        options: [
          { value: '10', label: '/books', childOf: '1' },
          { value: '11', label: '/more-books', childOf: '1' },
          { value: '20', label: '/audio', childOf: '2' },
        ],
      },
    ];

    const values = {
      LIBRARY_ID: '1',
      PATH_ID: '10',
    };

    expect(normalizeDependentSelectValues(fields, values)).toEqual(values);
  });

  it('clears cascading descendants after an intermediate select becomes invalid', () => {
    const fields: SettingsTab['fields'] = [
      {
        key: 'PROVIDER',
        label: 'Provider',
        type: 'SelectField',
        value: 'manual',
        options: [
          { value: 'manual', label: 'Manual' },
          { value: 'remote', label: 'Remote' },
        ],
      },
      {
        key: 'REMOTE_TYPE',
        label: 'Remote Type',
        type: 'SelectField',
        value: 'sftp',
        filterByField: 'PROVIDER',
        options: [{ value: 'sftp', label: 'SFTP', childOf: 'remote' }],
      },
      {
        key: 'REMOTE_REGION',
        label: 'Region',
        type: 'SelectField',
        value: 'eu',
        filterByField: 'REMOTE_TYPE',
        options: [{ value: 'eu', label: 'Europe', childOf: 'sftp' }],
      },
    ];

    expect(
      normalizeDependentSelectValues(fields, {
        PROVIDER: 'manual',
        REMOTE_TYPE: 'sftp',
        REMOTE_REGION: 'eu',
      }),
    ).toEqual({
      PROVIDER: 'manual',
      REMOTE_TYPE: '',
      REMOTE_REGION: '',
    });
  });
});
