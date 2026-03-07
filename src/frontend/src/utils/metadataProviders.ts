import { ContentType } from '../types';

export const getConfiguredMetadataProviderForContentType = ({
  contentType,
  configuredMetadataProvider,
  configuredAudiobookMetadataProvider,
}: {
  contentType: ContentType;
  configuredMetadataProvider: string | null;
  configuredAudiobookMetadataProvider: string | null;
}): string | null => {
  if (contentType === 'audiobook') {
    return configuredAudiobookMetadataProvider || configuredMetadataProvider || null;
  }

  return configuredMetadataProvider || null;
};
