import {
  AdvancedFilterState,
  Language,
  MetadataSearchField,
  ContentType,
  QueryTargetOption,
  SearchMode,
  MetadataProviderSummary,
} from '../types';
import { AdvancedFilters } from './AdvancedFilters';
import { SearchBar } from './SearchBar';

interface SearchSectionProps {
  onSearch: () => void;
  isLoading: boolean;
  isInitialState: boolean;
  bookLanguages: Language[];
  defaultLanguage: string[];
  logoUrl: string;
  queryValue: string | number | boolean;
  queryValueLabel?: string;
  onQueryValueChange: (value: string | number | boolean, label?: string) => void;
  queryTargets: QueryTargetOption[];
  activeQueryTarget: string;
  onQueryTargetChange: (key: string) => void;
  showAdvanced: boolean;
  onAdvancedToggle?: () => void;
  advancedFilters: AdvancedFilterState;
  onAdvancedFiltersChange: (updates: Partial<AdvancedFilterState>) => void;
  contentType?: ContentType;
  onContentTypeChange?: (type: ContentType) => void;
  allowedContentTypes?: ContentType[];
  activeQueryField?: MetadataSearchField | null;
  searchMode: SearchMode;
  onSearchModeChange: (mode: SearchMode) => void;
  metadataProviders?: MetadataProviderSummary[];
  activeMetadataProvider?: string | null;
  onMetadataProviderChange?: (provider: string) => void;
  isAdmin?: boolean;
}

export const SearchSection = ({
  onSearch,
  isLoading,
  isInitialState,
  bookLanguages,
  defaultLanguage,
  logoUrl,
  queryValue,
  queryValueLabel,
  onQueryValueChange,
  queryTargets,
  activeQueryTarget,
  onQueryTargetChange,
  showAdvanced,
  onAdvancedToggle,
  advancedFilters,
  onAdvancedFiltersChange,
  contentType = 'ebook',
  onContentTypeChange,
  allowedContentTypes,
  activeQueryField,
  searchMode,
  onSearchModeChange,
  metadataProviders,
  activeMetadataProvider,
  onMetadataProviderChange,
  isAdmin = false,
}: SearchSectionProps) => {
  return (
    <section
      id="search-section"
      className={`${
        isInitialState
          ? 'search-initial-state mb-6'
          : 'mb-3 sm:mb-4'
      } ${showAdvanced ? 'search-advanced-visible' : ''}`}
    >
      <div className={`flex items-center justify-center gap-3 transition-all duration-300 ${
        isInitialState ? 'opacity-100 mb-6 sm:mb-8' : 'opacity-0 h-0 mb-0 overflow-hidden'
      }`}>
        <img src={logoUrl} alt="Logo" className="h-8 w-8" />
        <h1 className="text-2xl font-semibold">Book Search & Download</h1>
      </div>
      <div className={`flex flex-col gap-3 search-wrapper transition-all duration-500 ${
        isInitialState ? '' : 'hidden'
      }`}>
        <SearchBar
          value={queryValue}
          valueLabel={queryValueLabel}
          onChange={onQueryValueChange}
          onSubmit={onSearch}
          isLoading={isLoading}
          onAdvancedToggle={onAdvancedToggle}
          isAdvancedActive={showAdvanced}
          contentType={contentType}
          onContentTypeChange={onContentTypeChange}
          allowedContentTypes={allowedContentTypes}
          queryTargets={queryTargets}
          activeQueryTarget={activeQueryTarget}
          onQueryTargetChange={onQueryTargetChange}
          activeQueryField={activeQueryField}
        />
        {activeQueryTarget === 'manual' && (
          <p className="text-xs opacity-50 px-2">
            Manual search queries release sources directly. Some sources may return limited metadata, which can affect file naming templates.
          </p>
        )}
        <AdvancedFilters
          visible={showAdvanced}
          bookLanguages={bookLanguages}
          defaultLanguage={defaultLanguage}
          filters={advancedFilters}
          onFiltersChange={onAdvancedFiltersChange}
          formClassName="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
          renderWrapper={form => form}
          searchMode={searchMode}
          onSearchModeChange={onSearchModeChange}
          metadataProviders={metadataProviders}
          activeMetadataProvider={activeMetadataProvider}
          onMetadataProviderChange={onMetadataProviderChange}
          contentType={contentType}
          isAdmin={isAdmin}
          onClose={onAdvancedToggle}
        />
      </div>
    </section>
  );
};
