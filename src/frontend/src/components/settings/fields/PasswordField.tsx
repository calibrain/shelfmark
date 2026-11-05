import { useState } from 'react';

import type { PasswordFieldConfig } from '../../../types/settings';
import { SECRET_PLACEHOLDER } from '../../../utils/settingsValues';

interface PasswordFieldProps {
  field: PasswordFieldConfig;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  /**
   * Fetches the stored secret that a masked value stands for. The eye button
   * calls it the first time a masked field is shown; without it the mask stays.
   */
  onReveal?: () => Promise<string>;
}

const MASKED_PLACEHOLDER_TEXT = 'Saved. Show it with the eye, or type a new value.';

export const PasswordField = ({
  field,
  value,
  onChange,
  disabled,
  onReveal,
}: PasswordFieldProps) => {
  const [showPassword, setShowPassword] = useState(false);
  const [revealedValue, setRevealedValue] = useState<string | null>(null);
  const [isRevealing, setIsRevealing] = useState(false);
  // disabled prop is already computed by SettingsContent.getDisabledState()
  const isDisabled = disabled ?? false;

  // A masked value means a secret is stored but was not sent. The input stays
  // empty until the user reveals it or types a replacement.
  const isMasked = value === SECRET_PLACEHOLDER;
  const isRevealed = isMasked && revealedValue !== null;
  const inputValue = isMasked ? (revealedValue ?? '') : value;
  const isShowing = showPassword && (!isMasked || isRevealed);

  const handleChange = (nextValue: string) => {
    setRevealedValue(null);
    onChange(nextValue);
  };

  const toggleVisibility = async () => {
    if (isShowing) {
      setShowPassword(false);
      return;
    }
    if (isMasked && !isRevealed && onReveal) {
      setIsRevealing(true);
      try {
        setRevealedValue(await onReveal());
        setShowPassword(true);
      } catch {
        // The secret stays hidden; nothing about it changed.
      } finally {
        setIsRevealing(false);
      }
      return;
    }
    setShowPassword(true);
  };

  return (
    <div className="relative">
      <input
        type={isShowing ? 'text' : 'password'}
        value={inputValue}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={isMasked && !isRevealed ? MASKED_PLACEHOLDER_TEXT : field.placeholder}
        aria-label={field.label || field.placeholder || 'Password field'}
        disabled={isDisabled}
        className="w-full rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 pr-10 text-sm transition-colors focus:border-sky-500 focus:ring-2 focus:ring-sky-500/50 focus:outline-hidden disabled:cursor-not-allowed disabled:opacity-60"
      />
      <button
        type="button"
        onClick={() => {
          void toggleVisibility();
        }}
        disabled={isDisabled || isRevealing}
        className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 transition-colors hover:bg-(--hover-action) disabled:cursor-not-allowed disabled:opacity-60"
        tabIndex={-1}
        aria-label={isShowing ? 'Hide password' : 'Show password'}
      >
        {isShowing ? (
          <svg
            className="h-4 w-4 opacity-60"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88"
            />
          </svg>
        ) : (
          <svg
            className="h-4 w-4 opacity-60"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z"
            />
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
            />
          </svg>
        )}
      </button>
    </div>
  );
};
