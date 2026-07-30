import { useSearchParams } from 'react-router-dom';

import { AdminSettingsPage, SelfSettingsPage } from '../components/settings';
import { getSettingsPath, resolveSettingsSection } from '../utils/settingsRoute';

interface SettingsPageProps {
  isAdmin: boolean;
  authMode: string;
  onShowToast: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved: () => void;
  onRefreshAuth: () => Promise<void>;
}

export const SettingsPage = ({
  isAdmin,
  authMode,
  onShowToast,
  onSettingsSaved,
  onRefreshAuth,
}: SettingsPageProps) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const section = resolveSettingsSection(searchParams.get('section'), isAdmin, authMode);
  const selectSection = (nextSection: 'personal' | 'admin') =>
    setSearchParams(new URLSearchParams(getSettingsPath(nextSection).split('?')[1]));

  return (
    <section className="py-4 sm:py-6">
      <header className="mb-6 border-b border-(--border-muted) pb-4">
        <h1 className="text-2xl font-semibold">Settings</h1>
        {isAdmin && authMode !== 'none' && (
          <nav className="mt-4 flex gap-2" aria-label="Settings section">
            {(['personal', 'admin'] as const).map((candidate) => (
              <button
                key={candidate}
                type="button"
                onClick={() => selectSection(candidate)}
                className={`rounded-lg px-3 py-2 text-sm font-medium capitalize ${section === candidate ? 'bg-violet-500/15 text-violet-700 dark:text-violet-300' : 'hover-surface'}`}
              >
                {candidate === 'personal' ? 'Personal' : 'Admin'}
              </button>
            ))}
          </nav>
        )}
      </header>
      {section === 'personal' ? (
        <SelfSettingsPage onShowToast={onShowToast} onSettingsSaved={onSettingsSaved} />
      ) : (
        <AdminSettingsPage
          authMode={authMode}
          onShowToast={onShowToast}
          onSettingsSaved={onSettingsSaved}
          onRefreshAuth={onRefreshAuth}
        />
      )}
    </section>
  );
};
