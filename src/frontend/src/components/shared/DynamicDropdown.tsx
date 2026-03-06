import { useEffect, useMemo, useState } from 'react';
import { DropdownList, DropdownListOption } from '../DropdownList';
import { DynamicFieldOption, fetchFieldOptions } from '../../services/api';

const optionsCache = new Map<string, DynamicFieldOption[]>();
const OPTIONS_CACHE_MAX = 50;

interface DynamicDropdownProps {
  endpoint: string;
  value: string;
  onChange: (value: string, label?: string) => void;
  placeholder?: string;
  widthClassName?: string;
  buttonClassName?: string;
  triggerChrome?: 'default' | 'minimal';
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
  widthClassName,
  buttonClassName,
  triggerChrome = 'default',
}: DynamicDropdownProps) => {
  const cachedOptions = optionsCache.get(endpoint) ?? [];
  const [options, setOptions] = useState<DynamicFieldOption[]>(cachedOptions);
  const [isLoading, setIsLoading] = useState(cachedOptions.length === 0);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const load = async () => {
      if (optionsCache.has(endpoint)) {
        setOptions(optionsCache.get(endpoint) ?? []);
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setLoadError(null);

      try {
        const loaded = await fetchFieldOptions(endpoint);
        if (!isMounted) {
          return;
        }
        if (optionsCache.size >= OPTIONS_CACHE_MAX) {
          const oldest = optionsCache.keys().next().value;
          if (oldest !== undefined) optionsCache.delete(oldest);
        }
        optionsCache.set(endpoint, loaded);
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
      widthClassName={widthClassName}
      buttonClassName={buttonClassName}
      triggerChrome={triggerChrome}
    />
  );
};
