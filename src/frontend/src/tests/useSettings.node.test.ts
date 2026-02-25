import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { mergeFetchedSettingsWithDirtyValues } from '../utils/settingsValues.js';

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

    assert.deepEqual(
      mergeFetchedSettingsWithDirtyValues(fetchedValues, currentValues, originalValues),
      {
        general: {
          apiKey: 'unsaved-key',
          endpoint: 'https://saved.example',
        },
      }
    );
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

    assert.deepEqual(
      mergeFetchedSettingsWithDirtyValues(fetchedValues, currentValues, originalValues),
      {
        provider: {
          host: 'new-host',
          enabled: true,
        },
      }
    );
  });
});
