import { useEffect, useMemo, useState } from 'react';
import { DropdownList, DropdownListOption } from '../DropdownList';
import { DynamicFieldOption, fetchFieldOptions } from '../../services/api';

interface DynamicDropdownProps {
  endpoint: string;
  value: string;
  onChange: (value: string, label?: string) => void;
  placeholder?: string;
}

const buildOptions = (
  options: DynamicFieldOption[],
): DropdownListOption[] => {
  return options.map((option) => ({
    value: option.value,
    label: option.label,
    description: option.description,
  }));
};

export const DynamicDropdown = ({
  endpoint,
  value,
  onChange,
  placeholder = 'Select...',
}: DynamicDropdownProps) => {
  const [options, setOptions] = useState<DynamicFieldOption[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const load = async () => {
      setIsLoading(true);
      setLoadError(null);

      try {
        const loaded = await fetchFieldOptions(endpoint);
        if (!isMounted) {
          return;
        }
        setOptions(loaded);
      } catch (error) {
        if (!isMounted) {
          return;
        }
        console.error('Failed to load dynamic dropdown options:', error);
        setOptions([]);
        setLoadError('Failed to load options');
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    void load();
    return () => {
      isMounted = false;
    };
  }, [endpoint]);

  const dropdownOptions = useMemo(() => {
    if (isLoading) {
      return [{ value: '__loading', label: 'Loading...', disabled: true }];
    }

    if (loadError) {
      return [{ value: '__error', label: loadError, disabled: true }];
    }

    return buildOptions(options);
  }, [isLoading, loadError, options]);

  const handleChange = (nextValue: string[] | string) => {
    const normalized = Array.isArray(nextValue) ? nextValue[0] ?? '' : nextValue;
    const match = options.find((opt) => opt.value === normalized);
    onChange(normalized, match?.label);
  };

  return (
    <DropdownList
      options={dropdownOptions}
      value={value}
      onChange={handleChange}
      placeholder={placeholder}
    />
  );
};
