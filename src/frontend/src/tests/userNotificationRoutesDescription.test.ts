import { describe, expect, it } from 'vitest';

import { buildSelfRoutesDescription } from '../components/settings/users/UserNotificationOverridesSection';

describe('buildSelfRoutesDescription', () => {
  it('does not point a user at the admin default or mailtos', () => {
    const text = buildSelfRoutesDescription(false, false);

    expect(text).not.toContain('admin default');
    expect(text).not.toContain('mailtos');
    expect(text).not.toContain('administrator');
  });

  it('says a default applies until the user adds their own', () => {
    expect(buildSelfRoutesDescription(true, false)).toContain(
      'default route that applies to you until you add your own',
    );
  });

  it('says their routes replace the default once they have set some', () => {
    expect(buildSelfRoutesDescription(true, true)).toContain('replace the default');
  });
});
