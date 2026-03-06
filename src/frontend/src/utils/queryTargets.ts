import { MetadataSearchField, QueryTargetOption, SearchMode, TextSearchField } from '../types';

const makeDirectField = (
  key: 'isbn' | 'author' | 'title',
  label: string,
  description: string,
): TextSearchField => ({
  key,
  label,
  type: 'TextSearchField',
  placeholder: `${label}…`,
  description,
});

const GENERAL_QUERY_TARGET: QueryTargetOption = {
  key: 'general',
  label: 'General',
  description: 'Search across all supported fields.',
  source: 'general',
};

const DIRECT_QUERY_TARGETS: QueryTargetOption[] = [
  GENERAL_QUERY_TARGET,
  {
    key: 'isbn',
    label: 'ISBN',
    description: 'Search for an exact ISBN.',
    source: 'direct-field',
    field: makeDirectField('isbn', 'ISBN', 'Search by ISBN'),
  },
  {
    key: 'author',
    label: 'Author',
    description: 'Search by author name.',
    source: 'direct-field',
    field: makeDirectField('author', 'Author', 'Search by author name'),
  },
  {
    key: 'title',
    label: 'Title',
    description: 'Search by title.',
    source: 'direct-field',
    field: makeDirectField('title', 'Title', 'Search by title'),
  },
];

const mapMetadataFieldToTarget = (field: MetadataSearchField): QueryTargetOption => ({
  key: field.key,
  label: field.label,
  description: field.description,
  source: 'provider-field',
  field,
});

export const buildQueryTargets = ({
  searchMode,
  metadataSearchFields = [],
  manualSearchAllowed = false,
}: {
  searchMode: SearchMode;
  metadataSearchFields?: MetadataSearchField[];
  manualSearchAllowed?: boolean;
}): QueryTargetOption[] => {
  if (searchMode === 'direct') {
    return DIRECT_QUERY_TARGETS;
  }

  const targets: QueryTargetOption[] = [
    GENERAL_QUERY_TARGET,
    ...metadataSearchFields.map(mapMetadataFieldToTarget),
  ];

  if (manualSearchAllowed) {
    targets.push({
      key: 'manual',
      label: 'Manual',
      description: 'Search release sources directly.',
      source: 'manual',
    });
  }

  return targets;
};

export const getDefaultQueryTargetKey = (targets: QueryTargetOption[]): string => {
  return targets[0]?.key || 'general';
};
