import { describe, expect, it } from 'vitest';

import { getSettingsPath, resolveSettingsSection } from '../utils/settingsRoute';

describe('settings routes', () => {
  it('keeps non-administrators in Personal settings', () => {
    expect(resolveSettingsSection('admin', false)).toBe('personal');
    expect(resolveSettingsSection('personal', false)).toBe('personal');
  });

  it('lets administrators select either settings subsection', () => {
    expect(resolveSettingsSection('personal', true)).toBe('personal');
    expect(resolveSettingsSection('admin', true)).toBe('admin');
    expect(resolveSettingsSection(null, true)).toBe('personal');
  });

  it('uses stable paths for settings entry points', () => {
    expect(getSettingsPath()).toBe('/settings');
    expect(getSettingsPath('personal')).toBe('/settings?section=personal');
    expect(getSettingsPath('admin')).toBe('/settings?section=admin');
  });
});
