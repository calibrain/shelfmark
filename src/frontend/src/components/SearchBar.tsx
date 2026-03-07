import {
  InputHTMLAttributes,
  RefObject,
  forwardRef,
  startTransition,
  useDeferredValue,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useSearchMode } from '../contexts/SearchModeContext';
import {
  ContentType,
  MetadataSearchField,
  QueryTargetOption,
  SortOption,
} from '../types';
import { DynamicFieldOption, fetchFieldOptions } from '../services/api';

interface SearchBarProps {
  value: string | number | boolean;
  valueLabel?: string;
  onChange: (value: string | number | boolean, label?: string) => void;
  onSubmit: () => void;
  isLoading?: boolean;
  onAdvancedToggle?: () => void;
  placeholder?: string;
  inputAriaLabel?: string;
  className?: string;
  inputClassName?: string;
  controlsClassName?: string;
  clearButtonLabel?: string;
  clearButtonTitle?: string;
  advancedButtonLabel?: string;
  advancedButtonTitle?: string;
  searchButtonLabel?: string;
  searchButtonTitle?: string;
  autoComplete?: string;
  enterKeyHint?: InputHTMLAttributes<HTMLInputElement>['enterKeyHint'];
  contentType?: ContentType;
  onContentTypeChange?: (type: ContentType) => void;
  allowedContentTypes?: ContentType[];
  queryTargets?: QueryTargetOption[];
  activeQueryTarget?: string;
  onQueryTargetChange?: (target: string) => void;
  activeQueryField?: MetadataSearchField | null;
  disabled?: boolean;
}

export interface SearchBarHandle {
  submit: () => void;
}

const useDismiss = (
  isOpen: boolean,
  refs: RefObject<HTMLElement | null>[],
  onClose: () => void,
) => {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const refsRef = useRef(refs);
  refsRef.current = refs;

  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (refsRef.current.some((r) => r.current?.contains(target))) return;
      onCloseRef.current();
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);
};

const autocompleteOptionsCache = new Map<string, DynamicFieldOption[]>();
const AUTOCOMPLETE_CACHE_MAX = 100;

const BookIcon = () => (
  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor" aria-hidden="true">
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
  </svg>
);

const AudiobookIcon = () => (
  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor" aria-hidden="true">
    <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
  </svg>
);

const getDefaultPlaceholder = (
  contentType: ContentType,
  activeQueryTarget: QueryTargetOption | undefined,
  fallback?: string,
): string => {
  if (fallback) return fallback;

  if (!activeQueryTarget || activeQueryTarget.source === 'general') {
    return contentType === 'ebook' ? 'Search Books' : 'Search Audiobooks';
  }

  if (activeQueryTarget.source === 'manual') {
    return 'Search releases directly…';
  }

  const field = activeQueryTarget.field;
  if (field?.placeholder) {
    return field.placeholder;
  }

  return `Search by ${activeQueryTarget.label.toLowerCase()}…`;
};

const hasActiveValue = (value: string | number | boolean): boolean => {
  if (typeof value === 'string') {
    return value.trim().length > 0;
  }
  if (typeof value === 'number') {
    return true;
  }
  return value;
};

const getClearedValue = (field?: MetadataSearchField | null): string | boolean => {
  if (field?.type === 'CheckboxSearchField') {
    return false;
  }
  return '';
};

export const SearchBar = forwardRef<SearchBarHandle, SearchBarProps>(({
  value,
  valueLabel,
  onChange,
  onSubmit,
  isLoading = false,
  onAdvancedToggle,
  placeholder,
  inputAriaLabel = 'Search books',
  className = '',
  inputClassName = '',
  controlsClassName = '',
  clearButtonLabel = 'Clear search input',
  clearButtonTitle = 'Clear search',
  advancedButtonLabel = 'Search settings',
  advancedButtonTitle = 'Search settings',
  searchButtonLabel = 'Search books',
  searchButtonTitle = 'Search',
  autoComplete = 'off',
  enterKeyHint = 'search',
  contentType = 'ebook',
  onContentTypeChange,
  allowedContentTypes,
  queryTargets = [],
  activeQueryTarget = 'general',
  onQueryTargetChange,
  activeQueryField,
  disabled = false,
}, ref) => {
  const { searchMode } = useSearchMode();
  const inputRef = useRef<HTMLInputElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const onSubmitRef = useRef(onSubmit);
  onSubmitRef.current = onSubmit;
  const selectorRef = useRef<HTMLDivElement>(null);
  const hasSearchQuery = hasActiveValue(value);
  const [isSelectorOpen, setIsSelectorOpen] = useState(false);
  const [dynamicOptions, setDynamicOptions] = useState<SortOption[]>([]);
  const [isDynamicLoading, setIsDynamicLoading] = useState(false);
  const [isSelectOpen, setIsSelectOpen] = useState(false);
  const [autocompleteOptions, setAutocompleteOptions] = useState<DynamicFieldOption[]>([]);
  const [isAutocompleteLoading, setIsAutocompleteLoading] = useState(false);
  const [isAutocompleteOpen, setIsAutocompleteOpen] = useState(false);
  const [textInputValue, setTextInputValue] = useState('');
  const selectTriggerRef = useRef<HTMLButtonElement>(null);
  const selectPanelRef = useRef<HTMLDivElement>(null);
  const autocompletePanelRef = useRef<HTMLDivElement>(null);
  const deferredTextInputValue = useDeferredValue(textInputValue);

  const hasMultipleContentTypes = !allowedContentTypes || allowedContentTypes.length !== 1;
  const showContentTypeSelector = searchMode !== 'direct' && !!onContentTypeChange && hasMultipleContentTypes;
  const showQueryTargetSelector = showContentTypeSelector || queryTargets.length > 1;
  const inputPaddingClass = showQueryTargetSelector
    ? 'pl-3 rounded-r-full'
    : 'pl-4 rounded-full';
  const searchInputClass = [
    'w-full min-w-0 py-3 border-0 outline-none search-input bg-transparent',
    inputPaddingClass,
  ].join(' ');

  const activeTarget = useMemo(
    () => queryTargets.find((target) => target.key === activeQueryTarget) ?? queryTargets[0],
    [queryTargets, activeQueryTarget],
  );
  const showActiveTargetLabel = Boolean(activeTarget && activeTarget.source !== 'general');

  useDismiss(isSelectorOpen, [selectorRef], () => setIsSelectorOpen(false));
  useDismiss(isSelectOpen, [selectPanelRef, selectTriggerRef], () => setIsSelectOpen(false));
  useDismiss(isAutocompleteOpen, [autocompletePanelRef, inputRef], () => setIsAutocompleteOpen(false));

  // Close select dropdown when active field changes
  useEffect(() => {
    setIsSelectOpen(false);
    setIsAutocompleteOpen(false);
    setAutocompleteOptions([]);
  }, [activeQueryField?.key, activeQueryField?.type]);

  // Load dynamic options when a DynamicSelectSearchField is active
  const dynamicEndpoint =
    activeQueryField?.type === 'DynamicSelectSearchField'
      ? activeQueryField.options_endpoint
      : null;
  const autocompleteEndpoint =
    activeQueryField?.type === 'TextSearchField'
      ? activeQueryField.suggestions_endpoint ?? null
      : null;
  const autocompleteMinQueryLength =
    activeQueryField?.type === 'TextSearchField'
      ? activeQueryField.suggestions_min_query_length ?? 2
      : 2;
  const autocompleteEmptyMessage =
    activeQueryField?.key === 'author'
      ? 'No authors found'
      : activeQueryField?.key === 'title'
        ? 'No titles found'
        : activeQueryField?.key === 'series'
          ? 'No series found'
          : 'No suggestions found';

  useEffect(() => {
    if (!dynamicEndpoint) {
      setDynamicOptions([]);
      return;
    }

    let cancelled = false;
    setIsDynamicLoading(true);

    fetchFieldOptions(dynamicEndpoint).then((loaded) => {
      if (cancelled) return;
      setDynamicOptions(loaded.map((o) => ({ value: o.value, label: o.label })));
      setIsDynamicLoading(false);
    }).catch(() => {
      if (cancelled) return;
      setDynamicOptions([]);
      setIsDynamicLoading(false);
    });

    return () => { cancelled = true; };
  }, [dynamicEndpoint]);

  useEffect(() => {
    if (!autocompleteEndpoint && activeQueryField?.type !== 'TextSearchField') {
      setTextInputValue(typeof value === 'string' ? value : String(value ?? ''));
      return;
    }

    const nextValue =
      autocompleteEndpoint && valueLabel && typeof value === 'string' && value.trim() !== ''
        ? valueLabel
        : typeof value === 'string'
          ? value
          : String(value ?? '');

    setTextInputValue(nextValue);
  }, [activeQueryField?.key, activeQueryField?.type, autocompleteEndpoint, value, valueLabel]);

  useEffect(() => {
    if (!autocompleteEndpoint || !isAutocompleteOpen) {
      setAutocompleteOptions([]);
      setIsAutocompleteLoading(false);
      return;
    }

    const normalizedQuery = deferredTextInputValue.trim();
    if (normalizedQuery.length < autocompleteMinQueryLength) {
      setAutocompleteOptions([]);
      setIsAutocompleteLoading(false);
      return;
    }

    const cacheKey = `${autocompleteEndpoint}::${normalizedQuery.toLowerCase()}`;
    if (autocompleteOptionsCache.has(cacheKey)) {
      startTransition(() => {
        setAutocompleteOptions(autocompleteOptionsCache.get(cacheKey) ?? []);
      });
      setIsAutocompleteLoading(false);
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      setIsAutocompleteLoading(true);
      fetchFieldOptions(autocompleteEndpoint, normalizedQuery)
        .then((loaded) => {
          if (cancelled) return;
          if (autocompleteOptionsCache.size >= AUTOCOMPLETE_CACHE_MAX) {
            const oldest = autocompleteOptionsCache.keys().next().value;
            if (oldest !== undefined) autocompleteOptionsCache.delete(oldest);
          }
          autocompleteOptionsCache.set(cacheKey, loaded);
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
  }, [autocompleteEndpoint, autocompleteMinQueryLength, deferredTextInputValue, isAutocompleteOpen]);

  // Resolve options for any select-type field
  const selectOptions: SortOption[] = useMemo(() => {
    if (activeQueryField?.type === 'SelectSearchField') {
      return activeQueryField.options;
    }
    if (activeQueryField?.type === 'DynamicSelectSearchField') {
      return dynamicOptions;
    }
    return [];
  }, [activeQueryField, dynamicOptions]);

  const isSelectField = activeQueryField?.type === 'SelectSearchField' || activeQueryField?.type === 'DynamicSelectSearchField';

  useImperativeHandle(ref, () => ({
    submit: () => {
      buttonRef.current?.click();
    },
  }));

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      if (disabled) {
        e.preventDefault();
        return;
      }
      onSubmit();
      (e.target as HTMLInputElement).blur();
    }
  };

  const handleClearSearch = () => {
    onChange(getClearedValue(activeQueryField));
    setIsSelectOpen(false);
    setIsAutocompleteOpen(false);
    setAutocompleteOptions([]);
    setTextInputValue('');
    inputRef.current?.focus();
  };

  const handleContentTypeSelect = (type: ContentType) => {
    onContentTypeChange?.(type);
    setIsSelectorOpen(false);
  };

  const handleQueryTargetSelect = (targetKey: string) => {
    onQueryTargetChange?.(targetKey);
    setIsSelectorOpen(false);
  };

  const effectivePlaceholder = getDefaultPlaceholder(contentType, activeTarget, placeholder);
  const effectiveInputAriaLabel = activeTarget
    ? `${inputAriaLabel}: ${activeTarget.label}`
    : inputAriaLabel;

  const selectDropdownOpen = isSelectField && isSelectOpen && selectOptions.length > 0;
  const autocompleteDropdownOpen =
    Boolean(autocompleteEndpoint)
    && isAutocompleteOpen
    && textInputValue.trim().length >= autocompleteMinQueryLength;
  const wrapperClasses = ['relative flex items-center rounded-full border', className].filter(Boolean).join(' ').trim();
  const controlsClasses = [
    'flex items-center gap-1 pr-2 flex-shrink-0',
    controlsClassName,
  ]
    .filter(Boolean)
    .join(' ')
    .trim();

  const renderActiveInput = () => {
    if (!activeQueryField || activeQueryField.type === 'TextSearchField') {
      const inputName = activeQueryField ? `${activeQueryField.key}-search` : 'search-input';
      return (
        <input
          type="search"
          name={inputName}
          placeholder={effectivePlaceholder}
          aria-label={effectiveInputAriaLabel}
          disabled={disabled}
          autoComplete={autoComplete}
          enterKeyHint={enterKeyHint}
          className={[
            searchInputClass,
            'search-input',
            disabled ? 'opacity-60 cursor-not-allowed' : '',
            inputClassName,
          ].filter(Boolean).join(' ')}
          style={{ color: 'var(--text)' }}
          value={autocompleteEndpoint ? textInputValue : (typeof value === 'string' ? value : String(value ?? ''))}
          onChange={(e) => {
            const nextValue = e.target.value;
            if (autocompleteEndpoint) {
              setTextInputValue(nextValue);
              setIsAutocompleteOpen(nextValue.trim().length >= autocompleteMinQueryLength);
              setIsSelectOpen(false);
              setIsSelectorOpen(false);
              onChange(nextValue);
              return;
            }
            onChange(nextValue);
          }}
          onFocus={() => {
            if (autocompleteEndpoint && textInputValue.trim().length >= autocompleteMinQueryLength) {
              setIsAutocompleteOpen(true);
            }
          }}
          onKeyDown={handleKeyDown}
          ref={inputRef}
        />
      );
    }

    switch (activeQueryField.type) {
      case 'NumberSearchField':
        return (
          <input
            type="number"
            name={`${activeQueryField.key}-search`}
            placeholder={effectivePlaceholder}
            aria-label={effectiveInputAriaLabel}
            disabled={disabled}
            enterKeyHint={enterKeyHint}
            min={activeQueryField.min}
            max={activeQueryField.max}
            step={activeQueryField.step}
            className={[
              searchInputClass,
              disabled ? 'opacity-60 cursor-not-allowed' : '',
              inputClassName,
            ].filter(Boolean).join(' ')}
            style={{ color: 'var(--text)' }}
            value={
              typeof value === 'number'
                ? value
                : typeof value === 'string'
                  ? value
                  : ''
            }
            onChange={(e) => {
              const raw = e.target.value;
              if (!raw) {
                onChange('');
                return;
              }
              const nextValue = Number.parseInt(raw, 10);
              if (!Number.isNaN(nextValue)) {
                onChange(nextValue);
              }
            }}
            onKeyDown={handleKeyDown}
            ref={inputRef}
          />
        );

      case 'SelectSearchField':
      case 'DynamicSelectSearchField': {
        const currentValue = typeof value === 'string' ? value : String(value ?? '');
        const selectedOption = selectOptions.find((o) => o.value === currentValue);
        return (
          <button
            ref={selectTriggerRef}
            type="button"
            onClick={() => {
              if (!disabled && !isDynamicLoading) {
                setIsSelectOpen((prev) => !prev);
                setIsSelectorOpen(false);
              }
            }}
            disabled={disabled}
            className={[
              'w-full text-left py-3 flex items-center gap-2',
              showQueryTargetSelector ? 'pl-3' : 'pl-4',
              'pr-2',
              disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer',
            ].filter(Boolean).join(' ')}
            style={{ color: 'var(--text)' }}
            aria-haspopup="listbox"
            aria-expanded={isSelectOpen}
          >
            {isDynamicLoading ? (
              <span className="opacity-50 truncate">Loading…</span>
            ) : selectedOption ? (
              <span className="truncate">{selectedOption.label}</span>
            ) : (
              <span className="opacity-50 truncate">{effectivePlaceholder}</span>
            )}
            <svg
              className={`w-3.5 h-3.5 opacity-40 flex-shrink-0 transition-transform duration-200 ${isSelectOpen ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth="2"
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
            </svg>
          </button>
        );
      }

      case 'CheckboxSearchField':
        return (
          <label className="flex min-w-0 items-center gap-3 px-4 py-3">
            <input
              type="checkbox"
              checked={Boolean(value)}
              onChange={(e) => onChange(e.target.checked)}
              className="h-4 w-4 rounded border-[var(--border-muted)] text-emerald-500 focus:ring-emerald-500/50"
            />
            <span className="truncate text-sm" style={{ color: 'var(--text)' }}>
              {activeQueryField.label}
            </span>
          </label>
        );

      default:
        return null;
    }
  };

  return (
    <div
      className={wrapperClasses}
      style={{
        background: disabled ? 'var(--bg)' : 'var(--bg-soft)',
        borderColor: 'var(--border-muted)',
      }}
    >
        {showQueryTargetSelector && (
          <div className="relative flex-shrink-0 flex self-stretch" ref={selectorRef}>
            <button
              type="button"
              onClick={() => { setIsSelectorOpen((prev) => !prev); setIsSelectOpen(false); }}
              className="flex items-center gap-1.5 pl-5 pr-2 rounded-l-full transition-colors hover-action"
              style={{ color: 'var(--text)' }}
              aria-label={`Searching ${contentType === 'ebook' ? 'books' : 'audiobooks'} by ${activeTarget?.label ?? 'general'}. Click to change.`}
              aria-expanded={isSelectorOpen}
              aria-haspopup="dialog"
            >
              {contentType === 'ebook' ? <BookIcon /> : <AudiobookIcon />}
              {showActiveTargetLabel && (
                <span className="hidden max-w-24 truncate text-sm font-medium sm:inline">
                  {activeTarget?.label}
                </span>
              )}
              <svg
                className={`w-3 h-3 opacity-50 transition-transform duration-200 ${isSelectorOpen ? 'rotate-180' : ''}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                strokeWidth="2.5"
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
              </svg>
            </button>

            <div
              className="absolute right-0 top-1/2 -translate-y-1/2 w-px h-6"
              style={{ background: 'var(--border-muted)' }}
            />

            {isSelectorOpen && (
              <div
                className="absolute left-0 top-full z-50 mt-2 w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border shadow-2xl animate-fade-in-down"
                style={{
                  background: 'var(--bg)',
                  borderColor: 'var(--border-muted)',
                }}
                role="dialog"
                aria-label="Search context"
              >
                <div className="max-h-[min(24rem,calc(100vh-8rem))] overflow-y-auto p-3">
                  {showContentTypeSelector && (
                    <div className="border-b pb-3" style={{ borderColor: 'var(--border-muted)' }}>
                      <div className="px-1 pb-2 text-xs font-medium uppercase tracking-wide opacity-60">
                        Content
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <button
                          type="button"
                          onClick={() => handleContentTypeSelect('ebook')}
                          className={`flex items-center gap-2 rounded-xl border px-3 py-2.5 text-sm font-medium transition-colors ${
                            contentType === 'ebook' ? 'bg-emerald-600 text-white' : 'hover-surface'
                          }`}
                          style={contentType !== 'ebook'
                            ? { color: 'var(--text)', borderColor: 'var(--border-muted)' }
                            : { borderColor: 'rgb(16 185 129 / 0.7)' }}
                        >
                          <BookIcon />
                          <span>Books</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => handleContentTypeSelect('audiobook')}
                          className={`flex items-center gap-2 rounded-xl border px-3 py-2.5 text-sm font-medium transition-colors ${
                            contentType === 'audiobook' ? 'bg-emerald-600 text-white' : 'hover-surface'
                          }`}
                          style={contentType !== 'audiobook'
                            ? { color: 'var(--text)', borderColor: 'var(--border-muted)' }
                            : { borderColor: 'rgb(16 185 129 / 0.7)' }}
                        >
                          <AudiobookIcon />
                          <span>Audiobooks</span>
                        </button>
                      </div>
                    </div>
                  )}

                  <div className={showContentTypeSelector ? 'pt-3' : ''}>
                    <div className="px-1 pb-2 text-xs font-medium uppercase tracking-wide opacity-60">
                      Search By
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      {queryTargets.map((target) => {
                        const isActive = target.key === activeTarget?.key;
                        return (
                          <button
                            type="button"
                            key={target.key}
                            onClick={() => handleQueryTargetSelect(target.key)}
                            title={target.description || target.label}
                            aria-label={target.label}
                            className={`min-w-0 rounded-xl border px-3 py-2.5 text-left text-sm font-medium transition-colors ${
                              isActive ? `${searchMode === 'direct' ? 'bg-sky-700' : 'bg-emerald-600'} text-white` : 'hover-surface'
                            }`}
                            style={isActive
                              ? { borderColor: searchMode === 'direct' ? 'rgb(3 105 161 / 0.7)' : 'rgb(16 185 129 / 0.7)' }
                              : { color: 'var(--text)', borderColor: 'var(--border-muted)' }}
                          >
                            <span className="block truncate">{target.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="min-w-0 flex-1">
          {renderActiveInput()}
        </div>

      <div className={controlsClasses}>
        {hasSearchQuery && (
          <button
            type="button"
            onClick={handleClearSearch}
            className="p-2 rounded-full hover-action flex items-center justify-center transition-colors"
            aria-label={clearButtonLabel}
            title={clearButtonTitle}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
              className="w-5 h-5"
              style={{ color: 'var(--text)' }}
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        )}
        {onAdvancedToggle && (
          <button
            type="button"
            onClick={onAdvancedToggle}
            className="p-2 rounded-full hover-action flex items-center justify-center transition-colors"
            aria-label={advancedButtonLabel}
            title={advancedButtonTitle}
          >
            <svg
              className="w-5 h-5"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
              style={{ color: 'var(--text)' }}
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75"
              />
            </svg>
          </button>
        )}
        <button
          ref={buttonRef}
          type="button"
          onClick={onSubmit}
          className={`p-2 my-2 rounded-full text-white disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center transition-colors search-bar-button ${
            searchMode === 'universal'
              ? 'bg-emerald-600 hover:bg-emerald-700'
              : 'bg-sky-700 hover:bg-sky-800'
          }`}
          aria-label={searchButtonLabel}
          title={searchButtonTitle}
          disabled={isLoading}
        >
          {!isLoading && (
            <svg
              className="w-5 h-5 search-bar-icon"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="2"
              stroke="currentColor"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
              />
            </svg>
          )}
          {isLoading && (
            <div className="spinner w-3 h-3 border-2 border-white border-t-transparent search-bar-spinner" />
          )}
        </button>
      </div>

      {selectDropdownOpen && (
        <div
          ref={selectPanelRef}
          className="absolute top-full left-0 right-0 z-50 mt-2 rounded-2xl border shadow-xl overflow-hidden animate-fade-in-down"
          style={{ background: 'var(--bg)', borderColor: 'var(--border-muted)' }}
          role="listbox"
          aria-label={effectiveInputAriaLabel}
        >
          <div className="max-h-64 overflow-y-auto py-1.5">
            {selectOptions.map((option) => {
              const currentValue = typeof value === 'string' ? value : String(value ?? '');
              const isSelected = option.value === currentValue;
              return (
                <button
                  type="button"
                  key={option.value}
                  role="option"
                  aria-selected={isSelected}
                  onClick={() => {
                    onChange(option.value, option.label);
                    setIsSelectOpen(false);
                    setTimeout(() => onSubmitRef.current(), 0);
                  }}
                  className={`w-full px-5 py-2.5 text-left text-sm flex items-center gap-3 transition-colors ${
                    isSelected ? '' : 'hover-surface'
                  }`}
                  style={{ color: 'var(--text)' }}
                >
                  <span className={`flex-1 truncate ${isSelected ? 'font-medium' : ''}`}>
                    {option.label}
                  </span>
                  {isSelected && (
                    <svg className="w-4 h-4 text-emerald-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth="2.5" stroke="currentColor" aria-hidden="true">
                      <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                    </svg>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {autocompleteDropdownOpen && (
        <div
          ref={autocompletePanelRef}
          className="absolute top-full left-0 right-0 z-50 mt-2 rounded-2xl border shadow-xl overflow-hidden animate-fade-in-down"
          style={{ background: 'var(--bg)', borderColor: 'var(--border-muted)' }}
          role="listbox"
          aria-label={`${effectiveInputAriaLabel} suggestions`}
        >
          <div className="max-h-72 overflow-y-auto py-1.5">
            {isAutocompleteLoading && (
              <div className="px-5 py-3 text-sm opacity-70" style={{ color: 'var(--text)' }}>
                Searching…
              </div>
            )}

            {!isAutocompleteLoading && autocompleteOptions.length === 0 && (
              <div className="px-5 py-3 text-sm opacity-70" style={{ color: 'var(--text)' }}>
                {autocompleteEmptyMessage}
              </div>
            )}

            {!isAutocompleteLoading && autocompleteOptions.map((option) => (
              <button
                type="button"
                key={option.value}
                role="option"
                onClick={() => {
                  setTextInputValue(option.label);
                  onChange(option.value, option.label);
                  setIsAutocompleteOpen(false);
                  setTimeout(() => onSubmitRef.current(), 0);
                }}
                className="w-full px-5 py-3 text-left text-sm transition-colors hover-surface"
                style={{ color: 'var(--text)' }}
              >
                <div className="truncate font-medium">{option.label}</div>
                {option.description && (
                  <div className="truncate text-xs opacity-70 mt-0.5">{option.description}</div>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});
