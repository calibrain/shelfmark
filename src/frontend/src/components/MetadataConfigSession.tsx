import { useMountEffect } from '@/hooks/useMountEffect';
import { getMetadataSearchConfig } from '@/services/api';
import type { ContentType, MetadataSearchConfig } from '@/types';

interface MetadataConfigSessionProps {
  contentType: ContentType;
  metadataProvider: string | null;
  onResolved: (config: MetadataSearchConfig | null) => void;
}

export const MetadataConfigSession = ({
  contentType,
  metadataProvider,
  onResolved,
}: MetadataConfigSessionProps) => {
  useMountEffect(() => {
    let cancelled = false;

    void getMetadataSearchConfig(contentType, metadataProvider ?? undefined)
      .then((nextConfig) => {
        if (cancelled) {
          return;
        }
        onResolved(nextConfig);
      })
      .catch((error) => {
        console.error('Failed to load metadata search config:', error);
        if (cancelled) {
          return;
        }
        onResolved(null);
      });

    return () => {
      cancelled = true;
    };
  });

  return null;
};
