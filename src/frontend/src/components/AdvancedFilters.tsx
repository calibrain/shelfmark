import { ReactNode } from 'react';
import {
  AdvancedFilterState,
  ContentType,
  Language,
  MetadataProviderSummary,
  SearchMode,
} from '../types';
import { normalizeLanguageSelection } from '../utils/languageFilters';
import { LanguageMultiSelect } from './LanguageMultiSelect';
import { DropdownList } from './DropdownList';
import { CONTENT_OPTIONS } from '../data/filterOptions';

const FORMAT_TYPES = ['pdf', 'epub', 'mobi', 'azw3', 'fb2', 'djvu', 'cbz', 'cbr', 'zip', 'rar'] as const;

interface AdvancedFiltersProps {
  visible: boolean;
  bookLanguages: Language[];
  defaultLanguage: string[];
  filters: AdvancedFilterState;
  onFiltersChange: (updates: Partial<AdvancedFilterState>) => void;
  formClassName?: string;
  renderWrapper?: (form: ReactNode) => ReactNode;
  searchMode: SearchMode;
  onSearchModeChange: (mode: SearchMode) => void;
  metadataProviders?: MetadataProviderSummary[];
  activeMetadataProvider?: string | null;
  onMetadataProviderChange?: (provider: string) => void;
  contentType?: ContentType;
  isAdmin?: boolean;
  onClose?: () => void;
}

const SEARCH_MODE_OPTIONS = [
  { value: 'direct', label: 'Direct', description: 'Search web sources for books and download directly. Works out of the box.' },
  { value: 'universal', label: 'Universal', description: 'Metadata-based search with downloads from all sources. Book and Audiobook support.' },
];

export const AdvancedFilters = ({
  visible,
  bookLanguages,
  defaultLanguage,
  filters,
  onFiltersChange,
  formClassName,
  renderWrapper,
  searchMode,
  onSearchModeChange,
  metadataProviders = [],
  activeMetadataProvider,
  onMetadataProviderChange,
  contentType = 'ebook',
  isAdmin = false,
  onClose,
}: AdvancedFiltersProps) => {
  const { lang, content, formats } = filters;

  const handleLangChange = (next: string[]) => {
    const normalized = normalizeLanguageSelection(next);
    onFiltersChange({ lang: normalized });
  };

  const handleContentChange = (next: string[] | string) => {
    const value = Array.isArray(next) ? next[0] ?? '' : next;
    onFiltersChange({ content: value });
  };

  const handleFormatsChange = (next: string[] | string) => {
    const nextFormats = Array.isArray(next) ? next : next ? [next] : [];
    onFiltersChange({ formats: nextFormats });
  };

  const formatOptions = FORMAT_TYPES.map(format => ({
    value: format,
    label: format.toUpperCase(),
  }));

  const providerOptions = metadataProviders.map((provider) => {
    const details: string[] = [];
    if (!provider.enabled) details.push('Disabled in Settings');
    if (provider.enabled && !provider.available) details.push('Not configured');
    if (provider.requires_auth) details.push('API key required');

    return {
      value: provider.name,
      label: provider.display_name,
      description: details.length > 0 ? details.join(' • ') : undefined,
      disabled: !provider.enabled || !provider.available,
    };
  });

  if (!visible) return null;

  const wrapperClassName = formClassName
    ? 'px-2'
    : 'px-2 lg:ml-16 lg:w-[calc(50vw+4rem)]';

  const settingsForm = (
    <div className={wrapperClassName}>
      {onClose && (
        <div className="flex justify-end mb-1">
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-full hover-action transition-colors"
            aria-label="Close filters"
            title="Close filters"
          >
            <svg
              className="w-4 h-4"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="2"
              stroke="currentColor"
              style={{ color: 'var(--text-muted)' }}
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}
      {isAdmin && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <DropdownList
              label="Search Mode"
              options={SEARCH_MODE_OPTIONS}
              value={searchMode}
              onChange={(value) => {
                const next = Array.isArray(value) ? value[0] ?? 'direct' : value;
                onSearchModeChange(next === 'universal' ? 'universal' : 'direct');
              }}
              placeholder="Choose a mode"
              widthClassName="w-full"
            />

            {searchMode === 'universal' && (
              <DropdownList
                label={contentType === 'audiobook' ? 'Audiobook Metadata Provider' : 'Book Metadata Provider'}
                options={providerOptions}
                value={activeMetadataProvider ?? ''}
                onChange={(value) => {
                  const next = Array.isArray(value) ? value[0] ?? '' : value;
                  onMetadataProviderChange?.(next);
                }}
                placeholder="Choose a provider"
                widthClassName="w-full"
              />
            )}
          </div>
        </>
      )}

      {searchMode === 'direct' && (
        <div className="space-y-4">
          <form
            id="search-filters"
            className={
              formClassName ??
              'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4'
            }
          >
            <LanguageMultiSelect
              options={bookLanguages}
              value={lang}
              onChange={handleLangChange}
              defaultLanguageCodes={defaultLanguage}
              label="Language"
            />
            <DropdownList
              label="Content"
              options={CONTENT_OPTIONS}
              value={content}
              onChange={handleContentChange}
              placeholder="All"
            />
            <DropdownList
              label="Formats"
              placeholder="Any"
              options={formatOptions}
              value={formats}
              onChange={handleFormatsChange}
              multiple
              showCheckboxes
              keepOpenOnSelect
            />
          </form>
        </div>
      )}
    </div>
  );

  return renderWrapper ? (
    renderWrapper(settingsForm)
  ) : (
    <div className="w-full border-b pt-6 pb-4 mb-4" style={{ borderColor: 'var(--border-muted)' }}>
      <div className="w-full px-4 sm:px-6 lg:px-8">{settingsForm}</div>
    </div>
  );
};
