export type SettingsSection = 'personal' | 'admin';

export const resolveSettingsSection = (
  requestedSection: string | null,
  isAdmin: boolean,
  authMode: string,
): SettingsSection => {
  if (authMode === 'none') {
    return 'admin';
  }
  return isAdmin && requestedSection === 'admin' ? 'admin' : 'personal';
};

export const getSettingsPath = (section?: SettingsSection): string =>
  section ? `/settings?section=${section}` : '/settings';
