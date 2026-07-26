import type { ReactNode } from 'react';

import type { AdvancedFilterState, ContentType, Language, MetadataProviderSummary } from '../types';
import { DropdownList } from './DropdownList';

interface AdvancedFiltersProps {
  visible: boolean;
  bookLanguages?: Language[];
  defaultLanguage?: string[];
  filters?: AdvancedFilterState;
  onFiltersChange?: (updates: Partial<AdvancedFilterState>) => void;
  formClassName?: string;
  renderWrapper?: (form: ReactNode) => ReactNode;
  metadataProviders?: MetadataProviderSummary[];
  activeMetadataProvider?: string | null;
  onMetadataProviderChange?: (provider: string) => void;
  contentType?: ContentType;
  combinedMode?: boolean;
  isAdmin?: boolean;
  onClose?: () => void;
}

const EMPTY_PROVIDERS: MetadataProviderSummary[] = [];

export const AdvancedFilters = ({
  visible,
  formClassName,
  renderWrapper,
  metadataProviders = EMPTY_PROVIDERS,
  activeMetadataProvider,
  onMetadataProviderChange,
  contentType = 'ebook',
  combinedMode = false,
  isAdmin = false,
  onClose,
}: AdvancedFiltersProps) => {
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

  let metadataProviderLabel = 'Book Metadata Provider';
  if (combinedMode) {
    metadataProviderLabel = 'Combined Metadata Provider';
  } else if (contentType === 'audiobook') {
    metadataProviderLabel = 'Audiobook Metadata Provider';
  }

  if (!visible) return null;

  const wrapperClassName = formClassName ? 'px-2' : 'px-2 lg:ml-16 lg:w-[calc(50vw+4rem)]';

  const settingsForm = (
    <div className={wrapperClassName}>
      {onClose && (
        <div className="mb-1 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="hover-action rounded-full p-1 transition-colors"
            aria-label="Close filters"
            title="Close filters"
          >
            <svg
              className="h-4 w-4"
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
        <div className="mb-4">
          <DropdownList
            label={metadataProviderLabel}
            options={providerOptions}
            value={activeMetadataProvider ?? ''}
            onChange={(value) => {
              const next = Array.isArray(value) ? (value[0] ?? '') : value;
              onMetadataProviderChange?.(next);
            }}
            placeholder="Choose a provider"
            widthClassName="w-full"
          />
        </div>
      )}
    </div>
  );

  return renderWrapper ? (
    renderWrapper(settingsForm)
  ) : (
    <div className="mb-4 w-full border-b pt-6 pb-4" style={{ borderColor: 'var(--border-muted)' }}>
      <div className="w-full px-4 sm:px-6 lg:px-8">{settingsForm}</div>
    </div>
  );
};
