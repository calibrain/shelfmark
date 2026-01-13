import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  getOnboarding,
  saveOnboarding,
  skipOnboarding,
  executeSettingsAction,
  OnboardingStep,
} from '../services/api';
import {
  SettingsField,
  TextFieldConfig,
  PasswordFieldConfig,
  CheckboxFieldConfig,
  SelectFieldConfig,
  MultiSelectFieldConfig,
  HeadingFieldConfig,
  ActionButtonConfig,
  ActionResult,
} from '../types/settings';
import { FieldWrapper } from './settings/shared';
import {
  TextField,
  PasswordField,
  CheckboxField,
  SelectField,
  MultiSelectField,
  HeadingField,
  ActionButton,
} from './settings/fields';

interface OnboardingModalProps {
  isOpen: boolean;
  onClose: () => void;
  onComplete: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

// Check if a field should be visible based on showWhen condition
function isFieldVisible(
  field: SettingsField,
  values: Record<string, unknown>
): boolean {
  const showWhen = field.showWhen;
  if (!showWhen) return true;

  const currentValue = values[showWhen.field];

  // Handle notEmpty condition
  if (showWhen.notEmpty) {
    if (Array.isArray(currentValue)) {
      return currentValue.length > 0;
    }
    return currentValue !== undefined && currentValue !== null && currentValue !== '';
  }

  // Handle array of allowed values or single value
  return Array.isArray(showWhen.value)
    ? showWhen.value.includes(currentValue as string)
    : currentValue === showWhen.value;
}

// Check if a step should be visible based on its showWhen conditions (all must be true)
function isStepVisible(
  step: OnboardingStep,
  values: Record<string, unknown>
): boolean {
  if (!step.showWhen || step.showWhen.length === 0) return true;

  // All conditions must be true (AND logic)
  return step.showWhen.every((condition) => {
    const currentValue = values[condition.field];
    return currentValue === condition.value;
  });
}

// Render the appropriate field component based on type
const renderField = (
  field: SettingsField,
  value: unknown,
  onChange: (value: unknown) => void,
  onAction: () => Promise<ActionResult>,
  isDisabled: boolean
) => {
  switch (field.type) {
    case 'TextField':
      return (
        <TextField
          field={field as TextFieldConfig}
          value={(value as string) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'PasswordField':
      return (
        <PasswordField
          field={field as PasswordFieldConfig}
          value={(value as string) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'CheckboxField':
      return (
        <CheckboxField
          field={field as CheckboxFieldConfig}
          value={(value as boolean) ?? false}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'SelectField':
      return (
        <SelectField
          field={field as SelectFieldConfig}
          value={(value as string) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'MultiSelectField':
      return (
        <MultiSelectField
          field={field as MultiSelectFieldConfig}
          value={(value as string[]) ?? []}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'ActionButton':
      return (
        <ActionButton
          field={field as ActionButtonConfig}
          onAction={onAction}
          disabled={isDisabled}
        />
      );
    case 'HeadingField':
      return <HeadingField field={field as HeadingFieldConfig} />;
    default:
      return <div>Unknown field type</div>;
  }
};

export const OnboardingModal = ({
  isOpen,
  onClose,
  onComplete,
  onShowToast,
}: OnboardingModalProps) => {
  const [steps, setSteps] = useState<OnboardingStep[]>([]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch onboarding config on mount
  useEffect(() => {
    if (!isOpen) return;

    const fetchOnboarding = async () => {
      try {
        setIsLoading(true);
        setError(null);
        const config = await getOnboarding();
        setSteps(config.steps);
        setValues(config.values);
      } catch (err) {
        console.error('Failed to fetch onboarding config:', err);
        setError('Failed to load setup wizard');
      } finally {
        setIsLoading(false);
      }
    };

    fetchOnboarding();
  }, [isOpen]);

  // Get visible steps based on current values
  const visibleSteps = useMemo(() => {
    return steps.filter((step) => isStepVisible(step, values));
  }, [steps, values]);

  // Get current step
  const currentStep = visibleSteps[currentStepIndex];

  // Get visible fields for current step
  const visibleFields = useMemo(() => {
    if (!currentStep) return [];
    return currentStep.fields.filter((field) => isFieldVisible(field, values));
  }, [currentStep, values]);

  // Handle field value changes
  const handleChange = useCallback((key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  // Handle next step
  const handleNext = useCallback(() => {
    if (currentStepIndex < visibleSteps.length - 1) {
      setCurrentStepIndex(currentStepIndex + 1);
    }
  }, [currentStepIndex, visibleSteps.length]);

  // Handle previous step
  const handleBack = useCallback(() => {
    if (currentStepIndex > 0) {
      setCurrentStepIndex(currentStepIndex - 1);
    }
  }, [currentStepIndex]);

  // Handle close with animation
  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      onClose();
    }, 150);
  }, [onClose]);

  // Handle skip
  const handleSkip = useCallback(async () => {
    try {
      setIsSaving(true);
      await skipOnboarding();
      onShowToast?.('Setup skipped - using defaults', 'info');
      handleClose();
      onComplete();
    } catch (err) {
      console.error('Failed to skip onboarding:', err);
      onShowToast?.('Failed to skip setup', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [handleClose, onComplete, onShowToast]);

  // Handle finish (save and complete)
  const handleFinish = useCallback(async () => {
    try {
      setIsSaving(true);
      const result = await saveOnboarding(values);
      if (result.success) {
        onShowToast?.('Setup complete!', 'success');
        handleClose();
        onComplete();
      } else {
        onShowToast?.(result.message || 'Failed to save settings', 'error');
      }
    } catch (err) {
      console.error('Failed to save onboarding:', err);
      onShowToast?.('Failed to save settings', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [values, handleClose, onComplete, onShowToast]);

  // Handle action button (e.g., test connection)
  const handleAction = useCallback(
    async (fieldKey: string): Promise<ActionResult> => {
      if (!currentStep) {
        return { success: false, message: 'No current step' };
      }
      try {
        // Pass current values so actions can use them (e.g., API key for test connection)
        return await executeSettingsAction(currentStep.tab, fieldKey, values);
      } catch (err) {
        return {
          success: false,
          message: err instanceof Error ? err.message : 'Action failed',
        };
      }
    },
    [currentStep, values]
  );

  // Handle ESC key
  useEffect(() => {
    if (!isOpen) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, handleClose]);

  // Prevent body scroll when open
  useEffect(() => {
    if (isOpen) {
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }
  }, [isOpen]);

  if (!isOpen && !isClosing) return null;

  // Loading state
  if (isLoading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" />
        <div
          className="relative rounded-xl p-8 shadow-2xl"
          style={{ background: 'var(--bg)' }}
        >
          <div className="flex items-center gap-3">
            <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
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
            <span>Loading setup wizard...</span>
          </div>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={handleClose} />
        <div
          className="relative rounded-xl p-8 shadow-2xl max-w-md"
          style={{ background: 'var(--bg)' }}
        >
          <div className="text-center space-y-4">
            <div className="text-red-500">
              <svg
                className="w-12 h-12 mx-auto"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
                />
              </svg>
            </div>
            <p className="text-sm">{error}</p>
            <button
              onClick={handleClose}
              className="px-4 py-2 rounded-lg text-sm font-medium
                       bg-[var(--bg-soft)] border border-[var(--border-muted)]
                       hover:bg-[var(--hover-surface)] transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    );
  }

  const isFirstStep = currentStepIndex === 0;
  const isLastStep = currentStepIndex === visibleSteps.length - 1;
  const progress = ((currentStepIndex + 1) / visibleSteps.length) * 100;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className={`absolute inset-0 bg-black/50 backdrop-blur-sm transition-opacity duration-150
                    ${isClosing ? 'opacity-0' : 'opacity-100'}`}
      />

      {/* Modal */}
      <div
        className={`relative w-full max-w-xl rounded-xl
                    border border-[var(--border-muted)] shadow-2xl
                    ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-label="Setup Wizard"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-muted)]">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-8 h-8 rounded-full bg-sky-500/20 text-sky-500 text-sm font-medium">
              {currentStepIndex + 1}
            </div>
            <div>
              <h2 className="text-lg font-semibold">{currentStep?.title || 'Setup'}</h2>
              <p className="text-xs opacity-60">
                Step {currentStepIndex + 1} of {visibleSteps.length}
              </p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-lg hover:bg-[var(--hover-surface)] transition-colors"
            aria-label="Close"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              stroke="currentColor"
              className="w-5 h-5"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Progress bar */}
        <div className="h-1 bg-[var(--bg-soft)]">
          <div
            className="h-full bg-sky-500 transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>

        {/* Content */}
        <div className="px-6 py-5 space-y-5 min-h-[280px]">
          {visibleFields.map((field) => {
            const isDisabled = 'fromEnv' in field ? (field.fromEnv ?? false) : false;
            return (
              <FieldWrapper key={field.key} field={field}>
                {renderField(
                  field,
                  values[field.key],
                  (v) => handleChange(field.key, v),
                  () => handleAction(field.key),
                  isDisabled
                )}
              </FieldWrapper>
            );
          })}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-[var(--border-muted)] flex items-center justify-between h-[68px]">
          <div>
            <button
              onClick={handleSkip}
              disabled={isSaving || !isFirstStep}
              className={`px-4 py-2 rounded-lg text-sm font-medium
                         ${isFirstStep ? 'opacity-60 hover:opacity-100 transition-opacity' : 'invisible'}`}
            >
              Skip setup
            </button>
          </div>

          <div className="flex gap-3">
            {!isFirstStep && (
              <button
                onClick={handleBack}
                disabled={isSaving}
                className="px-4 py-2 rounded-lg text-sm font-medium
                         bg-[var(--bg-soft)] border border-[var(--border-muted)]
                         hover:bg-[var(--hover-surface)] transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Back
              </button>
            )}

            {isLastStep ? (
              <button
                onClick={handleFinish}
                disabled={isSaving}
                className="px-5 py-2 rounded-lg text-sm font-medium
                         bg-sky-600 text-white
                         hover:bg-sky-700 transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed
                         flex items-center gap-2"
              >
                {isSaving ? (
                  <>
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
                  </>
                ) : (
                  'Finish Setup'
                )}
              </button>
            ) : (
              <button
                onClick={handleNext}
                disabled={isSaving}
                className="px-5 py-2 rounded-lg text-sm font-medium
                         bg-sky-600 text-white
                         hover:bg-sky-700 transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed
                         flex items-center gap-1"
              >
                Next
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                  stroke="currentColor"
                  className="w-4 h-4"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
