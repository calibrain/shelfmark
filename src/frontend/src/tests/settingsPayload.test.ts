import { describe, expect, it } from 'vitest';

import { buildUserSettingsPayload } from '../components/settings/users/settingsPayload';
import type { DeliveryPreferencesResponse } from '../services/api';
import { SECRET_PLACEHOLDER } from '../utils/settingsValues';

const TOKEN_KEY = 'HARDCOVER_SYNC_TOKEN';

const hardcoverPreferences: DeliveryPreferencesResponse = {
  tab: 'hardcover_sync',
  keys: [TOKEN_KEY],
  fields: [],
  globalValues: { [TOKEN_KEY]: SECRET_PLACEHOLDER },
  userOverrides: { [TOKEN_KEY]: SECRET_PLACEHOLDER },
  effective: { [TOKEN_KEY]: { value: SECRET_PLACEHOLDER, source: 'user_override' } },
};

const deliveryPreferences: DeliveryPreferencesResponse = {
  tab: 'downloads',
  keys: ['DESTINATION'],
  fields: [],
  globalValues: { DESTINATION: '/books' },
  userOverrides: {},
  effective: { DESTINATION: { value: '/books', source: 'global_config' } },
};

describe('buildUserSettingsPayload', () => {
  it('sends a masked secret back as the placeholder instead of clearing it', () => {
    const payload = buildUserSettingsPayload(
      { [TOKEN_KEY]: SECRET_PLACEHOLDER },
      new Set([TOKEN_KEY]),
      [hardcoverPreferences],
    );

    expect(payload).toEqual({ [TOKEN_KEY]: SECRET_PLACEHOLDER });
  });

  it('sends a newly typed secret as it is', () => {
    const payload = buildUserSettingsPayload({ [TOKEN_KEY]: 'new-token' }, new Set([TOKEN_KEY]), [
      hardcoverPreferences,
    ]);

    expect(payload).toEqual({ [TOKEN_KEY]: 'new-token' });
  });

  it('still clears a plain value that matches the global one', () => {
    const payload = buildUserSettingsPayload({ DESTINATION: '/books' }, new Set(['DESTINATION']), [
      deliveryPreferences,
    ]);

    expect(payload).toEqual({ DESTINATION: null });
  });

  it('clears a secret the user removed', () => {
    const payload = buildUserSettingsPayload({}, new Set([TOKEN_KEY]), [hardcoverPreferences]);

    expect(payload).toEqual({ [TOKEN_KEY]: null });
  });
});
