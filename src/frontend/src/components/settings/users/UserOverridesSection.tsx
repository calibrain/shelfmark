import { ReactNode } from 'react';
import { BookloreOption, DownloadDefaults } from '../../../services/api';
import { OverrideKey, PerUserSettings } from './types';

const inputClasses =
  'w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500 transition-colors';

const disabledInputClasses =
  'w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm opacity-50 cursor-not-allowed transition-colors';

interface UserOverridesSectionProps {
  standalone?: boolean;
  downloadDefaults: DownloadDefaults | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
  overrides: Record<OverrideKey, boolean>;
  toggleOverride: (key: OverrideKey, enabled: boolean) => void;
  bookloreLibraries: BookloreOption[];
  booklorePaths: BookloreOption[];
}

export const UserOverridesSection = ({
  standalone = false,
  downloadDefaults,
  isUserOverridable,
  userSettings,
  setUserSettings,
  overrides,
  toggleOverride,
  bookloreLibraries,
  booklorePaths,
}: UserOverridesSectionProps) => {
  const outputMode = downloadDefaults?.BOOKS_OUTPUT_MODE || 'folder';
  const canOverrideDestination = isUserOverridable('DESTINATION') && (outputMode === 'folder' || outputMode === 'booklore');
  const canOverrideBookloreLibrary = isUserOverridable('BOOKLORE_LIBRARY_ID') && outputMode === 'booklore';
  const canOverrideBooklorePath = isUserOverridable('BOOKLORE_PATH_ID') && outputMode === 'booklore';
  const canOverrideEmailRecipients = isUserOverridable('EMAIL_RECIPIENTS') && outputMode === 'email';
  const showDownloadOverrides = canOverrideDestination || canOverrideBookloreLibrary || canOverrideBooklorePath || canOverrideEmailRecipients;

  if (!downloadDefaults || !showDownloadOverrides) {
    return null;
  }

  return (
    <>
      <div className={standalone ? '' : 'border-t border-[var(--border-muted)] pt-4'}>
        <p className="text-xs font-medium opacity-60 mb-1">Download Settings Overrides</p>
        <p className="text-xs opacity-40 mb-3">Override global defaults for this user.</p>
      </div>

      {canOverrideDestination && (
        <OverrideField
          label="Destination Folder"
          enabled={overrides.destination || false}
          onToggle={(v) => toggleOverride('destination', v)}
          globalValue={downloadDefaults.DESTINATION || '/books'}
        >
          <input
            type="text"
            value={userSettings.DESTINATION || ''}
            onChange={(e) => setUserSettings((s) => ({ ...s, DESTINATION: e.target.value }))}
            className={overrides.destination ? inputClasses : disabledInputClasses}
            disabled={!overrides.destination}
            placeholder={downloadDefaults.DESTINATION || '/books'}
          />
        </OverrideField>
      )}

      {(canOverrideBookloreLibrary || canOverrideBooklorePath) && (
        <>
          {canOverrideBookloreLibrary && (
            <OverrideField
              label="BookLore Library"
              enabled={overrides.booklore_library_id || false}
              onToggle={(v) => toggleOverride('booklore_library_id', v)}
              globalValue={
                bookloreLibraries.find((l) => l.value === downloadDefaults.BOOKLORE_LIBRARY_ID)?.label
                || downloadDefaults.BOOKLORE_LIBRARY_ID
                || 'Not set'
              }
            >
              <select
                value={userSettings.BOOKLORE_LIBRARY_ID || ''}
                onChange={(e) => {
                  setUserSettings((s) => ({ ...s, BOOKLORE_LIBRARY_ID: e.target.value, BOOKLORE_PATH_ID: '' }));
                }}
                className={overrides.booklore_library_id ? inputClasses : disabledInputClasses}
                disabled={!overrides.booklore_library_id}
              >
                <option value="">Select library...</option>
                {bookloreLibraries.map((lib) => (
                  <option key={lib.value} value={lib.value}>{lib.label}</option>
                ))}
              </select>
            </OverrideField>
          )}
          {canOverrideBooklorePath && (
            <OverrideField
              label="BookLore Path"
              enabled={overrides.booklore_path_id || false}
              onToggle={(v) => toggleOverride('booklore_path_id', v)}
              globalValue={
                booklorePaths.find((p) => p.value === downloadDefaults.BOOKLORE_PATH_ID)?.label
                || downloadDefaults.BOOKLORE_PATH_ID
                || 'Not set'
              }
            >
              <select
                value={userSettings.BOOKLORE_PATH_ID || ''}
                onChange={(e) => setUserSettings((s) => ({ ...s, BOOKLORE_PATH_ID: e.target.value }))}
                className={overrides.booklore_path_id ? inputClasses : disabledInputClasses}
                disabled={!overrides.booklore_path_id}
              >
                <option value="">Select path...</option>
                {booklorePaths
                  .filter((p) => {
                    const selectedLib = userSettings.BOOKLORE_LIBRARY_ID || downloadDefaults.BOOKLORE_LIBRARY_ID;
                    return !p.childOf || p.childOf === selectedLib;
                  })
                  .map((path) => (
                    <option key={path.value} value={path.value}>{path.label}</option>
                  ))}
              </select>
            </OverrideField>
          )}
        </>
      )}

      {canOverrideEmailRecipients && (
        <OverrideField
          label="Email Recipients"
          enabled={overrides.email_recipients || false}
          onToggle={(v) => toggleOverride('email_recipients', v)}
          globalValue={
            downloadDefaults.EMAIL_RECIPIENTS?.length
              ? downloadDefaults.EMAIL_RECIPIENTS.map((r) => r.nickname || r.email).join(', ')
              : 'None configured'
          }
        >
          {overrides.email_recipients && (
            <EmailRecipientsEditor
              recipients={userSettings.EMAIL_RECIPIENTS || []}
              onChange={(r) => setUserSettings((s) => ({ ...s, EMAIL_RECIPIENTS: r }))}
            />
          )}
        </OverrideField>
      )}
    </>
  );
};

interface OverrideFieldProps {
  label: string;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  globalValue: string;
  children: ReactNode;
}

const OverrideField = ({ label, enabled, onToggle, globalValue, children }: OverrideFieldProps) => (
  <div className="space-y-1.5">
    <div className="flex items-center justify-between">
      <label className="text-sm font-medium">{label}</label>
      <button
        type="button"
        onClick={() => onToggle(!enabled)}
        className={`text-xs px-2.5 py-1 rounded-lg font-medium transition-colors
          ${enabled
            ? 'bg-sky-500/15 text-sky-400 hover:bg-sky-500/25'
            : 'bg-zinc-500/10 opacity-60 hover:opacity-80'}`}
      >
        {enabled ? 'Custom' : 'Global'}
      </button>
    </div>
    {!enabled && (
      <p className="text-xs opacity-40">Using global: {globalValue}</p>
    )}
    {children}
  </div>
);

interface EmailRecipientsEditorProps {
  recipients: Array<{ nickname: string; email: string }>;
  onChange: (recipients: Array<{ nickname: string; email: string }>) => void;
}

const EmailRecipientsEditor = ({ recipients, onChange }: EmailRecipientsEditorProps) => {
  const addRecipient = () => {
    onChange([...recipients, { nickname: '', email: '' }]);
  };

  const removeRecipient = (index: number) => {
    onChange(recipients.filter((_, i) => i !== index));
  };

  const updateRecipient = (index: number, field: 'nickname' | 'email', value: string) => {
    const updated = [...recipients];
    updated[index] = { ...updated[index], [field]: value };
    onChange(updated);
  };

  return (
    <div className="space-y-2">
      {recipients.map((r, i) => (
        <div key={i} className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
          <input
            type="text"
            value={r.nickname}
            onChange={(e) => updateRecipient(i, 'nickname', e.target.value)}
            className={inputClasses}
            placeholder="Nickname"
          />
          <input
            type="email"
            value={r.email}
            onChange={(e) => updateRecipient(i, 'email', e.target.value)}
            className={inputClasses}
            placeholder="email@example.com"
          />
          <button
            type="button"
            onClick={() => removeRecipient(i)}
            className="text-xs font-medium px-2.5 py-1.5 rounded-lg border border-[var(--border-muted)] text-red-400 hover:bg-red-600 hover:text-white hover:border-red-600 transition-colors shrink-0"
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={addRecipient}
        className="text-xs font-medium px-3 py-2 rounded-lg border border-[var(--border-muted)]
                   hover:bg-[var(--hover-surface)] transition-colors"
      >
        + Add Recipient
      </button>
    </div>
  );
};
