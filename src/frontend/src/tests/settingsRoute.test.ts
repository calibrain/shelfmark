import { describe, expect, it } from 'vitest';

import { getSettingsPath, resolveSettingsSection } from '../utils/settingsRoute';

describe('settings routes', () => {
  it('keeps non-administrators in Personal settings', () => {
    expect(resolveSettingsSection('admin', false, 'builtin')).toBe('personal');
    expect(resolveSettingsSection('personal', false, 'builtin')).toBe('personal');
  });

  it('lets administrators select either settings subsection', () => {
    expect(resolveSettingsSection('personal', true, 'builtin')).toBe('personal');
    expect(resolveSettingsSection('admin', true, 'builtin')).toBe('admin');
    expect(resolveSettingsSection(null, true, 'builtin')).toBe('personal');
  });

  it('keeps unprotected instances out of Personal settings', () => {
    expect(resolveSettingsSection('personal', true, 'none')).toBe('admin');
    expect(resolveSettingsSection(null, true, 'none')).toBe('admin');
  });

  it('uses stable paths for settings entry points', () => {
    expect(getSettingsPath()).toBe('/settings');
    expect(getSettingsPath('personal')).toBe('/settings?section=personal');
    expect(getSettingsPath('admin')).toBe('/settings?section=admin');
  });
});
