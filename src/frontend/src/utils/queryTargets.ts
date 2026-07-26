import type { MetadataSearchField, QueryTargetOption } from '../types';

const GENERAL_QUERY_TARGET: QueryTargetOption = {
  key: 'general',
  label: 'General',
  description: 'Search across all supported fields.',
  source: 'general',
};

const mapMetadataFieldToTarget = (field: MetadataSearchField): QueryTargetOption => ({
  key: field.key,
  label: field.label,
  description: field.description,
  source: 'provider-field',
  field,
});

export const buildQueryTargets = ({
  metadataSearchFields = [],
}: {
  metadataSearchFields?: MetadataSearchField[];
}): QueryTargetOption[] => {
  return [GENERAL_QUERY_TARGET, ...metadataSearchFields.map(mapMetadataFieldToTarget)];
};

export const getDefaultQueryTargetKey = (targets: QueryTargetOption[]): string => {
  return targets[0]?.key || 'general';
};
