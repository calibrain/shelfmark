import { describe, expect, it } from 'vitest';

import { buildSelfSettingsPayload } from '../components/settings/SelfSettingsPage';

describe('personal settings payload', () => {
  it('uses the editable Email for email notifications without a second destination', () => {
    expect(
      buildSelfSettingsPayload({
        username: 'reader',
        email: 'reader@example.com',
        display_name: '',
        kindle_address: '',
        notifications_enabled: true,
        notification_transport: 'email',
        notification_destination: 'obsolete@example.com',
      }),
    ).toMatchObject({
      email: 'reader@example.com',
      notification_transport: null,
      notification_destination: null,
    });
  });

  it('retains a separate destination only for Apprise', () => {
    expect(
      buildSelfSettingsPayload({
        username: 'reader',
        email: 'reader@example.com',
        display_name: '',
        kindle_address: '',
        notifications_enabled: true,
        notification_transport: 'apprise',
        notification_destination: 'ntfys://ntfy.sh/reader',
      }),
    ).toMatchObject({
      notification_transport: 'apprise',
      notification_destination: 'ntfys://ntfy.sh/reader',
    });
  });
});
