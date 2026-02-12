interface SettingsSaveBarProps {
  onSave: () => void | Promise<void>;
  isSaving: boolean;
}

export const SettingsSaveBar = ({ onSave, isSaving }: SettingsSaveBarProps) => (
  <div
    className="flex-shrink-0 px-6 py-4 border-t border-[var(--border-muted)] bg-[var(--bg)] animate-slide-up"
    style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))' }}
  >
    <button
      onClick={() => { void onSave(); }}
      disabled={isSaving}
      className="w-full py-2.5 px-4 rounded-lg font-medium transition-colors
                 bg-sky-600 text-white hover:bg-sky-700
                 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {isSaving ? (
        <span className="flex items-center justify-center gap-2">
          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
              fill="none"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          Saving...
        </span>
      ) : (
        'Save Changes'
      )}
    </button>
  </div>
);
