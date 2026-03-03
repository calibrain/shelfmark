import { useEffect, useMemo, useState } from 'react';
import { DropdownList, DropdownListOption } from '../DropdownList';
import { DynamicFieldOption, fetchFieldOptions } from '../../services/api';

interface DynamicDropdownProps {
  endpoint: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  allLabel?: string;
}

const GROUP_HEADER_PREFIX = '__group__';

const buildOptions = (
  options: DynamicFieldOption[],
  allLabel: string
): DropdownListOption[] => {
  const built: DropdownListOption[] = [{ value: '', label: allLabel }];
  let previousGroup: string | null = null;

  options.forEach((option, index) => {
    if (option.group && option.group !== previousGroup) {
      previousGroup = option.group;
      built.push({
        value: `${GROUP_HEADER_PREFIX}${option.group}:${index}`,
        label: option.group,
        disabled: true,
      });
    } else if (!option.group) {
      previousGroup = null;
    }

    built.push({
      value: option.value,
      label: option.label,
      description: option.description,
    });
  });

  return built;
};

export const DynamicDropdown = ({
  endpoint,
  value,
  onChange,
  placeholder = 'Select an option',
  allLabel = 'All',
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
      return [
        { value: '', label: allLabel },
        { value: '__loading', label: 'Loading...', disabled: true },
      ];
    }

    if (loadError) {
      return [
        { value: '', label: allLabel },
        { value: '__error', label: loadError, disabled: true },
      ];
    }

    return buildOptions(options, allLabel);
  }, [allLabel, isLoading, loadError, options]);

  const handleChange = (nextValue: string[] | string) => {
    const normalized = Array.isArray(nextValue) ? nextValue[0] ?? '' : nextValue;
    if (normalized.startsWith(GROUP_HEADER_PREFIX)) {
      return;
    }
    onChange(normalized);
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
