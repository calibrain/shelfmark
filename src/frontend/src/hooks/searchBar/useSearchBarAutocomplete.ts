import {
  startTransition,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
} from 'react';

import type { DynamicFieldOption } from '../../services/api';
import { fetchFieldOptions } from '../../services/api';
import type { TextSearchField } from '../../types';

const autocompleteOptionsCache = new Map<string, DynamicFieldOption[]>();
const AUTOCOMPLETE_CACHE_MAX = 100;

interface AutocompleteTextState {
  draftValue: string;
  fieldKey: string | null;
  syncedValue: string;
}

interface UseSearchBarAutocompleteOptions {
  field: TextSearchField | null;
  value: string | number | boolean;
  valueLabel?: string;
  isOpen: boolean;
}

interface UseSearchBarAutocompleteReturn {
  autocompleteEndpoint: string | null;
  autocompleteMinQueryLength: number;
  textInputValue: string;
  autocompleteOptions: DynamicFieldOption[];
  isAutocompleteLoading: boolean;
  autocompleteEmptyMessage: string;
  setAutocompleteDraftValue: (nextValue: string) => void;
  setAutocompleteSelection: (value: string, label: string) => void;
  resetAutocomplete: () => void;
}

const getAutocompleteDisplayValue = (
  value: string | number | boolean,
  valueLabel: string | undefined,
): string => {
  let nextValue = typeof value === 'string' ? value : String(value ?? '');
  if (valueLabel && typeof value === 'string' && value.trim() !== '') {
    nextValue = valueLabel;
  }
  return nextValue;
};

const getAutocompleteEmptyMessage = (fieldKey: string | null): string => {
  if (fieldKey === 'author') {
    return 'No authors found';
  }
  if (fieldKey === 'title') {
    return 'No titles found';
  }
  if (fieldKey === 'series') {
    return 'No series found';
  }
  return 'No suggestions found';
};

const cacheAutocompleteOptions = (cacheKey: string, options: DynamicFieldOption[]): void => {
  if (autocompleteOptionsCache.size >= AUTOCOMPLETE_CACHE_MAX) {
    const oldest = autocompleteOptionsCache.keys().next().value;
    if (oldest !== undefined) {
      autocompleteOptionsCache.delete(oldest);
    }
  }
  autocompleteOptionsCache.set(cacheKey, options);
};

export const useSearchBarAutocomplete = ({
  field,
  value,
  valueLabel,
  isOpen,
}: UseSearchBarAutocompleteOptions): UseSearchBarAutocompleteReturn => {
  const autocompleteEndpoint = field?.suggestions_endpoint ?? null;
  const autocompleteMinQueryLength = field?.suggestions_min_query_length ?? 2;
  const autocompleteFieldKey = autocompleteEndpoint ? (field?.key ?? null) : null;
  const externalAutocompleteValue = autocompleteEndpoint
    ? getAutocompleteDisplayValue(value, valueLabel)
    : '';

  const [autocompleteTextState, setAutocompleteTextState] = useState<AutocompleteTextState>(() => ({
    draftValue: externalAutocompleteValue,
    fieldKey: autocompleteFieldKey,
    syncedValue: externalAutocompleteValue,
  }));
  const [autocompleteOptions, setAutocompleteOptions] = useState<DynamicFieldOption[]>([]);
  const [isAutocompleteLoading, setIsAutocompleteLoading] = useState(false);

  if (
    autocompleteTextState.fieldKey !== autocompleteFieldKey ||
    (autocompleteFieldKey !== null &&
      autocompleteTextState.syncedValue !== externalAutocompleteValue)
  ) {
    setAutocompleteTextState({
      draftValue: externalAutocompleteValue,
      fieldKey: autocompleteFieldKey,
      syncedValue: externalAutocompleteValue,
    });
  }

  const deferredTextInputValue = useDeferredValue(autocompleteTextState.draftValue);

  useEffect(() => {
    if (!autocompleteEndpoint || !isOpen) {
      setAutocompleteOptions([]);
      setIsAutocompleteLoading(false);
      return undefined;
    }

    const normalizedQuery = deferredTextInputValue.trim();
    if (normalizedQuery.length < autocompleteMinQueryLength) {
      setAutocompleteOptions([]);
      setIsAutocompleteLoading(false);
      return undefined;
    }

    const cacheKey = `${autocompleteEndpoint}::${normalizedQuery.toLowerCase()}`;
    if (autocompleteOptionsCache.has(cacheKey)) {
      startTransition(() => {
        setAutocompleteOptions(autocompleteOptionsCache.get(cacheKey) ?? []);
      });
      setIsAutocompleteLoading(false);
      return undefined;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      setIsAutocompleteLoading(true);
      fetchFieldOptions(autocompleteEndpoint, normalizedQuery)
        .then((loaded) => {
          if (cancelled) return;
          cacheAutocompleteOptions(cacheKey, loaded);
          startTransition(() => {
            setAutocompleteOptions(loaded);
          });
          setIsAutocompleteLoading(false);
        })
        .catch(() => {
          if (cancelled) return;
          startTransition(() => {
            setAutocompleteOptions([]);
          });
          setIsAutocompleteLoading(false);
        });
    }, 260);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [autocompleteEndpoint, autocompleteMinQueryLength, deferredTextInputValue, isOpen]);

  const autocompleteEmptyMessage = useMemo(
    () => getAutocompleteEmptyMessage(autocompleteFieldKey),
    [autocompleteFieldKey],
  );

  const setAutocompleteDraftValue = useCallback((nextValue: string) => {
    setAutocompleteTextState((current) => ({
      ...current,
      draftValue: nextValue,
      syncedValue: nextValue,
    }));
  }, []);

  const setAutocompleteSelection = useCallback(
    (nextValue: string, label: string) => {
      setAutocompleteTextState({
        draftValue: label,
        fieldKey: autocompleteFieldKey,
        syncedValue: nextValue,
      });
    },
    [autocompleteFieldKey],
  );

  const resetAutocomplete = useCallback(() => {
    setAutocompleteTextState((current) => ({
      ...current,
      draftValue: '',
      syncedValue: '',
      fieldKey: autocompleteFieldKey,
    }));
    setAutocompleteOptions([]);
    setIsAutocompleteLoading(false);
  }, [autocompleteFieldKey]);

  return {
    autocompleteEndpoint,
    autocompleteMinQueryLength,
    textInputValue: autocompleteTextState.draftValue,
    autocompleteOptions,
    isAutocompleteLoading,
    autocompleteEmptyMessage,
    setAutocompleteDraftValue,
    setAutocompleteSelection,
    resetAutocomplete,
  };
};
