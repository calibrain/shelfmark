import { DeliveryPreferencesResponse } from '../../../services/api';
import { PerUserSettings } from './types';
import { SettingsSubpage } from '../shared';
import { UserOverridesSection } from './UserOverridesSection';

interface UserOverridesViewProps {
  hasChanges: boolean;
  onBack: () => void;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
}

export const UserOverridesView = ({
  hasChanges,
  onBack,
  deliveryPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
}: UserOverridesViewProps) => (
  <SettingsSubpage hasBottomSaveBar={hasChanges}>
    <div className="space-y-5">
      <div>
        <button
          onClick={onBack}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border border-[var(--border-muted)]
                     bg-[var(--bg)] hover:bg-[var(--hover-surface)] transition-colors"
        >
          <svg
            className="w-4 h-4"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
          </svg>
          Back to User
        </button>
      </div>

      <UserOverridesSection
        deliveryPreferences={deliveryPreferences}
        isUserOverridable={isUserOverridable}
        userSettings={userSettings}
        setUserSettings={setUserSettings}
      />
    </div>
  </SettingsSubpage>
);
