import type { DeliveryPreferencesResponse } from '../../../services/api';
import type { HeadingFieldConfig, PasswordFieldConfig } from '../../../types/settings';
import { HeadingField, PasswordField } from '../fields';
import { FieldWrapper } from '../shared';
import { getFieldByKey, toTextValue } from './fieldHelpers';
import type { PerUserSettings } from './types';

interface UserHardcoverOverridesSectionProps {
  hardcoverPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
  onRevealSecret?: (key: string) => Promise<string>;
}

const TOKEN_KEY = 'HARDCOVER_SYNC_TOKEN';

const hardcoverHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'hardcover_account_heading',
  title: 'Hardcover Account',
  description: 'Connect your own Hardcover account. Your shelves then sync to requests you own.',
};

const fallbackTokenField: PasswordFieldConfig = {
  type: 'PasswordField',
  key: TOKEN_KEY,
  label: 'Hardcover API Token',
  description: 'Bearer token for your Hardcover account.',
  value: '',
};

export const UserHardcoverOverridesSection = ({
  hardcoverPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
  onRevealSecret,
}: UserHardcoverOverridesSectionProps) => {
  if (!hardcoverPreferences || !isUserOverridable(TOKEN_KEY)) {
    return null;
  }

  const tokenField = getFieldByKey(hardcoverPreferences.fields, TOKEN_KEY, fallbackTokenField);
  const tokenValue = toTextValue(userSettings[TOKEN_KEY]);
  // The stored token is masked and the global one too, so the only sign of an own
  // token is a non-empty value; comparing with the global value would say nothing.
  const hasOwnToken = tokenValue.length > 0;
  const isLocked = Boolean(tokenField.fromEnv);

  return (
    <div className="space-y-4">
      <HeadingField field={hardcoverHeading} />

      <FieldWrapper
        field={tokenField}
        resetAction={
          hasOwnToken
            ? {
                disabled: isLocked,
                onClick: () =>
                  setUserSettings((prev) => {
                    const next = { ...prev };
                    delete next[TOKEN_KEY];
                    return next;
                  }),
              }
            : undefined
        }
      >
        <PasswordField
          field={tokenField}
          value={tokenValue}
          onChange={(value) => setUserSettings((prev) => ({ ...prev, [TOKEN_KEY]: value }))}
          disabled={isLocked}
          onReveal={onRevealSecret ? () => onRevealSecret(TOKEN_KEY) : undefined}
        />
      </FieldWrapper>
    </div>
  );
};
