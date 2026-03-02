interface ToggleSwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  color?: 'sky' | 'emerald';
}

const colorClasses = {
  sky: { active: 'bg-sky-600', ring: 'focus:ring-sky-500/50' },
  emerald: { active: 'bg-emerald-600', ring: 'focus:ring-emerald-500/50' },
};

export const ToggleSwitch = ({
  checked,
  onChange,
  disabled = false,
  color = 'sky',
}: ToggleSwitchProps) => {
  const { active, ring } = colorClasses[color];

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => !disabled && onChange(!checked)}
      disabled={disabled}
      className={`relative inline-flex h-6 w-11 items-center rounded-full
                  transition-colors duration-200 focus:outline-none focus:ring-2
                  ${ring} disabled:opacity-60 disabled:cursor-not-allowed
                  ${checked ? active : 'bg-gray-300 dark:bg-gray-600'}`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white
                    shadow-sm transition-transform duration-200
                    ${checked ? 'translate-x-6' : 'translate-x-1'}`}
      />
    </button>
  );
};
