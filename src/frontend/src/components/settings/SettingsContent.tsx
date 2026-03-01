import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  SettingsTab,
  SettingsField,
  ActionResult,
  TextFieldConfig,
  PasswordFieldConfig,
  NumberFieldConfig,
  CheckboxFieldConfig,
  SelectFieldConfig,
  MultiSelectFieldConfig,
  TagListFieldConfig,
  OrderableListFieldConfig,
  OrderableListItem,
  ActionButtonConfig,
  HeadingFieldConfig,
  ShowWhenCondition,
  TableFieldConfig,
  CustomComponentFieldConfig,
} from '../../types/settings';
import { FieldWrapper, SettingsSaveBar } from './shared';
import {
  TextField,
  PasswordField,
  NumberField,
  CheckboxField,
  SelectField,
  MultiSelectField,
  TagListField,
  OrderableListField,
  ActionButton,
  HeadingField,
  TableField,
} from './fields';
import {
  CustomSettingsFieldLayout,
  getCustomSettingsFieldLayout,
  renderCustomSettingsField,
} from './customFields';

interface SettingsContentProps {
  tab: SettingsTab;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  onSave: () => Promise<void>;
  onAction: (key: string) => Promise<ActionResult>;
  isSaving: boolean;
  hasChanges: boolean;
  isUniversalMode?: boolean; // Whether app is in Universal search mode
  overrideSummary?: Record<string, { count: number; users: Array<{ userId: number; username: string; value: unknown }> }>;
  embedded?: boolean;
  customFieldContext?: {
    authMode?: string;
    onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
    onRefreshOverrideSummary?: () => void;
    onRefreshAuth?: () => Promise<void>;
    onSettingsSaved?: () => void;
  };
}

function evaluateShowWhenCondition(
  showWhen: ShowWhenCondition,
  values: Record<string, unknown>
): boolean {
  const currentValue = values[showWhen.field];

  if (showWhen.notEmpty) {
    if (Array.isArray(currentValue)) {
      return currentValue.length > 0;
    }
    return currentValue !== undefined && currentValue !== null && currentValue !== '';
  }

  return Array.isArray(showWhen.value)
    ? showWhen.value.includes(currentValue as string)
    : currentValue === showWhen.value;
}

// Check if a field should be visible based on showWhen condition and search mode
function isFieldVisible(
  field: SettingsField,
  values: Record<string, unknown>,
  isUniversalMode: boolean
): boolean {
  if ('hiddenInUi' in field && field.hiddenInUi) {
    return false;
  }

  // Check universalOnly - hide these fields in Direct mode
  if ('universalOnly' in field && field.universalOnly && !isUniversalMode) {
    return false;
  }

  const showWhen = field.showWhen;
  if (!showWhen) return true;

  if (Array.isArray(showWhen)) {
    return showWhen.every((condition) => evaluateShowWhenCondition(condition, values));
  }

  return evaluateShowWhenCondition(showWhen, values);
}

// Check if a field should be disabled based on disabledWhen condition
// Returns { disabled: boolean, reason?: string }
function getDisabledState(
  field: SettingsField,
  values: Record<string, unknown>
): { disabled: boolean; reason?: string } {
  // HeadingField doesn't have disabledWhen
  if (field.type === 'HeadingField') {
    return { disabled: false };
  }

  // Check if value is locked by environment variable
  if ('fromEnv' in field && field.fromEnv) {
    return { disabled: true };
  }

  // Check static disabled first
  if ('disabled' in field && field.disabled) {
    return {
      disabled: true,
      reason: 'disabledReason' in field ? field.disabledReason : undefined,
    };
  }

  // Check disabledWhen condition
  if (!('disabledWhen' in field) || !field.disabledWhen) {
    return { disabled: false };
  }

  const { field: conditionField, value: conditionValue, reason } = field.disabledWhen;
  const currentValue = values[conditionField];

  // Check if condition is met (handles both array and single value)
  const isDisabled = Array.isArray(conditionValue)
    ? conditionValue.includes(currentValue as string)
    : currentValue === conditionValue;

  return {
    disabled: isDisabled,
    reason: isDisabled ? reason : undefined,
  };
}

// Render the appropriate field component based on type
const renderField = (
  field: SettingsField,
  value: unknown,
  onChange: (value: unknown) => void,
  onAction: () => Promise<ActionResult>,
  isDisabled: boolean,
  allValues: Record<string, unknown>, // All form values for cascading dropdown support
  authMode?: string
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
    case 'NumberField':
      return (
        <NumberField
          field={field as NumberFieldConfig}
          value={(value as number) ?? 0}
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
    case 'SelectField': {
      const selectConfig = field as SelectFieldConfig;
      // Get filter value for cascading dropdowns
      const rawFilterValue = selectConfig.filterByField
        ? allValues[selectConfig.filterByField]
        : undefined;
      const filterValue =
        rawFilterValue === undefined || rawFilterValue === null || rawFilterValue === ''
          ? undefined
          : String(rawFilterValue);
      return (
        <SelectField
          field={selectConfig}
          value={(value as string) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
          filterValue={filterValue}
        />
      );
    }
    case 'MultiSelectField':
      return (
        <MultiSelectField
          field={field as MultiSelectFieldConfig}
          value={(value as string[]) ?? []}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'TagListField':
      return (
        <TagListField
          field={field as TagListFieldConfig}
          value={(value as string[]) ?? []}
          onChange={(v) => onChange(v)}
          disabled={isDisabled}
        />
      );
    case 'OrderableListField':
      return (
        <OrderableListField
          field={field as OrderableListFieldConfig}
          value={(value as OrderableListItem[]) ?? []}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'ActionButton':
      return <ActionButton field={field as ActionButtonConfig} onAction={onAction} disabled={isDisabled} />;
    case 'TableField':
      return (
        <TableField
          field={field as TableFieldConfig}
          value={(value as Record<string, unknown>[]) ?? []}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'HeadingField':
      {
        const headingField = field as HeadingFieldConfig;
        const normalizedAuthMode = String(authMode || '').toLowerCase();
        const dynamicDescription = headingField.descriptionByAuthMode
          ? (
              headingField.descriptionByAuthMode[normalizedAuthMode]
              ?? headingField.descriptionByAuthMode.default
              ?? headingField.descriptionByAuthMode.none
              ?? headingField.description
            )
          : headingField.description;

        return (
          <HeadingField
            field={{
              ...headingField,
              description: dynamicDescription,
            }}
          />
        );
      }
    default:
      return <div>Unknown field type</div>;
  }
};

export const SettingsContent = ({
  tab,
  values,
  onChange,
  onSave,
  onAction,
  isSaving,
  hasChanges,
  isUniversalMode = true,
  overrideSummary,
  embedded = false,
  customFieldContext,
}: SettingsContentProps) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [customFieldUiState, setCustomFieldUiState] = useState<Record<string, Record<string, unknown>>>({});

  // Reset scroll position when tab changes before paint.
  useLayoutEffect(() => {
    if (embedded) {
      return;
    }
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [embedded, tab.name]);

  useEffect(() => {
    setCustomFieldUiState({});
  }, [tab.name]);

  const updateCustomFieldUiState = useCallback((fieldKey: string, key: string, value: unknown) => {
    setCustomFieldUiState((prev) => {
      const previousFieldState = prev[fieldKey] || {};
      if (previousFieldState[key] === value) {
        return prev;
      }
      return {
        ...prev,
        [fieldKey]: {
          ...previousFieldState,
          [key]: value,
        },
      };
    });
  }, []);

  // Memoize the visible fields to avoid recalculating on every render
  const baseVisibleFields = useMemo(
    () => tab.fields.filter((field) => isFieldVisible(field, values, isUniversalMode)),
    [tab.fields, values, isUniversalMode]
  );

  const customFieldLayouts = useMemo(() => {
    const layouts: Record<string, CustomSettingsFieldLayout> = {};
    baseVisibleFields.forEach((field) => {
      if (field.type !== 'CustomComponentField') {
        return;
      }
      layouts[field.key] = getCustomSettingsFieldLayout({
        field: field as CustomComponentFieldConfig,
        tab,
        values,
        uiState: customFieldUiState[field.key] || {},
      });
    });
    return layouts;
  }, [baseVisibleFields, tab, values, customFieldUiState]);

  const activeTakeOverFieldKey = useMemo(() => {
    for (const field of baseVisibleFields) {
      if (field.type !== 'CustomComponentField') {
        continue;
      }
      if (customFieldLayouts[field.key]?.takeOverTab) {
        return field.key;
      }
    }
    return null;
  }, [baseVisibleFields, customFieldLayouts]);

  // Reset scroll when entering/leaving a custom subpage takeover before paint.
  useLayoutEffect(() => {
    if (embedded) {
      return;
    }
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [embedded, activeTakeOverFieldKey]);

  const visibleFields = useMemo(() => {
    if (!activeTakeOverFieldKey) {
      return baseVisibleFields;
    }
    return baseVisibleFields.filter((field) => field.key === activeTakeOverFieldKey);
  }, [activeTakeOverFieldKey, baseVisibleFields]);

  const activeTakeOverLayout = activeTakeOverFieldKey
    ? customFieldLayouts[activeTakeOverFieldKey]
    : undefined;
  const isTakeOverActive = Boolean(activeTakeOverFieldKey);
  const customSaveBar = activeTakeOverLayout?.saveBar;

  const saveBarOnSave = isTakeOverActive
    ? customSaveBar?.onSave
    : onSave;
  const saveBarIsSaving = isTakeOverActive
    ? Boolean(customSaveBar?.isSaving)
    : isSaving;
  const saveBarHasChanges = isTakeOverActive
    ? Boolean(customSaveBar?.hasChanges && customSaveBar?.onSave)
    : hasChanges;

  const renderedFields = (
    <div className="space-y-5">
      {visibleFields.map((field) => {
        const disabledState = getDisabledState(field, values);
        const fieldOverrideSummary = overrideSummary?.[field.key];

        const renderedField = field.type === 'CustomComponentField'
          ? renderCustomSettingsField({
              field: field as CustomComponentFieldConfig,
              tab,
              values,
              onChange,
              onAction,
              uiState: customFieldUiState[field.key] || {},
              onUiStateChange: (key, value) => updateCustomFieldUiState(field.key, key, value),
              isDisabled: disabledState.disabled,
              disabledReason: disabledState.reason,
              authMode: customFieldContext?.authMode,
              onShowToast: customFieldContext?.onShowToast,
              onRefreshOverrideSummary: customFieldContext?.onRefreshOverrideSummary,
              onRefreshAuth: customFieldContext?.onRefreshAuth,
              onSettingsSaved: customFieldContext?.onSettingsSaved,
            })
          : renderField(
              field,
              values[field.key],
              (v) => onChange(field.key, v),
              () => onAction(field.key),
              disabledState.disabled,
              values,
              customFieldContext?.authMode
            );

        const shouldWrapInFieldWrapper = !(
          field.type === 'CustomComponentField' && !(field as CustomComponentFieldConfig).wrapInFieldWrapper
        );

        if (!shouldWrapInFieldWrapper) {
          return (
            <div key={`${tab.name}-${field.key}`}>
              {renderedField}
            </div>
          );
        }

        return (
          <FieldWrapper
            key={`${tab.name}-${field.key}`}
            field={field}
            disabledOverride={disabledState.disabled}
            disabledReasonOverride={disabledState.reason}
            userOverrideCount={fieldOverrideSummary?.count}
            userOverrideDetails={fieldOverrideSummary?.users}
          >
            {renderedField}
          </FieldWrapper>
        );
      })}
    </div>
  );

  const saveButton = saveBarHasChanges && saveBarOnSave ? (
    <button
      onClick={() => { void saveBarOnSave(); }}
      disabled={saveBarIsSaving}
      className="w-full py-2.5 px-4 rounded-lg font-medium transition-colors
                 bg-sky-600 text-white hover:bg-sky-700
                 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {saveBarIsSaving ? (
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
  ) : null;

  if (embedded) {
    return (
      <div className="space-y-5">
        {renderedFields}
        {saveButton}
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Scrollable content area */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-6"
        style={{ paddingBottom: saveBarHasChanges ? 'calc(5rem + env(safe-area-inset-bottom))' : '1.5rem' }}
      >
        {renderedFields}
      </div>

      {/* Save button - only visible when there are changes */}
      {saveBarHasChanges && saveBarOnSave && (
        <SettingsSaveBar onSave={saveBarOnSave} isSaving={saveBarIsSaving} />
      )}
    </div>
  );
};
