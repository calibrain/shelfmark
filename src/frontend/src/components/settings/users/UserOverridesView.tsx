import { AdminUser, DeliveryPreferencesResponse } from '../../../services/api';
import { PerUserSettings } from './types';
import { SettingsSubpage } from '../shared';
import { UserAuthSourceBadge } from './UserAuthSourceBadge';
import { UserOverridesSection } from './UserOverridesSection';

interface UserOverridesViewProps {
  user: AdminUser;
  onSave: () => void;
  saving: boolean;
  onBack: () => void;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
}

export const UserOverridesView = ({
  user,
  onSave,
  saving,
  onBack,
  deliveryPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
}: UserOverridesViewProps) => (
  <SettingsSubpage>
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <UserAuthSourceBadge user={user} />
      </div>

      <UserOverridesSection
        standalone
        deliveryPreferences={deliveryPreferences}
        isUserOverridable={isUserOverridable}
        userSettings={userSettings}
        setUserSettings={setUserSettings}
      />

      <div className="flex gap-2 pt-2">
        <button
          onClick={onSave}
          disabled={saving}
          className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-sky-600 hover:bg-sky-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
        <button
          onClick={onBack}
          className="px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)]
                     bg-[var(--bg-soft)] hover:bg-[var(--hover-surface)] transition-colors"
        >
          Back
        </button>
      </div>
    </div>
  </SettingsSubpage>
);
