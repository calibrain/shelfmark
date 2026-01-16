import { useEffect, useMemo, useRef } from 'react';
import { SelectFieldConfig } from '../../../types/settings';
import { DropdownList } from '../../DropdownList';

interface SelectFieldProps {
  field: SelectFieldConfig;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  filterValue?: string;
}

export const SelectField = ({ field, value, onChange, disabled, filterValue }: SelectFieldProps) => {
  const isDisabled = disabled ?? false;

  const prevFilterValue = useRef(filterValue);

  const normalizedOptions = useMemo(
    () =>
      field.options.map((opt) => ({
        ...opt,
        value: String(opt.value),
        childOf:
          opt.childOf === undefined || opt.childOf === null
            ? undefined
            : String(opt.childOf),
        label: opt.label ?? String(opt.value),
      })),
    [field.options]
  );

  // Filter options based on filterValue (cascading dropdown support)
  const filteredOptions = useMemo(() => {
    if (!filterValue) {
      return normalizedOptions.filter((opt) => !opt.childOf);
    }
    // Filter to options that belong to the selected parent or have no parent
    return normalizedOptions.filter((opt) => !opt.childOf || opt.childOf === filterValue);
  }, [normalizedOptions, filterValue]);

  // Clear selection when filter value changes and current value is not in filtered options
  useEffect(() => {
    if (prevFilterValue.current !== filterValue && filterValue !== undefined) {
      const currentValueInOptions = filteredOptions.some((opt) => opt.value === value);
      if (!currentValueInOptions && value) {
        onChange('');
      }
    }
    prevFilterValue.current = filterValue;
  }, [filterValue, filteredOptions, value, onChange]);

  // Use field's default value as fallback when value is empty
  const effectiveValue = value || field.default || '';

  // Convert options to DropdownList format
  const dropdownOptions = filteredOptions.map((opt) => ({
    value: opt.value,
    label: opt.label,
    description: opt.description,
  }));

  const handleChange = (newValue: string | string[]) => {
    // DropdownList may return string or string[] - we expect string for single select
    const val = Array.isArray(newValue) ? newValue[0] ?? '' : newValue;
    onChange(val);
  };

  if (isDisabled) {
    // When disabled, show a static display instead of the dropdown
    const selectedOption = filteredOptions.find((opt) => opt.value === effectiveValue);
    return (
      <div className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm opacity-60 cursor-not-allowed">
        {selectedOption?.label || 'Select...'}
      </div>
    );
  }

  return (
    <DropdownList
      options={dropdownOptions}
      value={effectiveValue}
      onChange={handleChange}
      placeholder="Select..."
      widthClassName="w-full"
    />
  );
};
