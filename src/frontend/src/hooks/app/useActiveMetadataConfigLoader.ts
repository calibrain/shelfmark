import { useEffect, useState } from 'react';

import { getMetadataSearchConfig } from '../../services/api';
import type { ContentType, MetadataSearchConfig, SearchMode } from '../../types';

interface UseActiveMetadataConfigLoaderOptions {
  isAuthenticated: boolean;
  effectiveSearchMode: SearchMode;
  effectiveContentType: ContentType;
  effectiveMetadataProvider: string | null;
}

export const useActiveMetadataConfigLoader = ({
  isAuthenticated,
  effectiveSearchMode,
  effectiveContentType,
  effectiveMetadataProvider,
}: UseActiveMetadataConfigLoaderOptions): MetadataSearchConfig | null => {
  const [activeMetadataConfig, setActiveMetadataConfig] = useState<MetadataSearchConfig | null>(
    null,
  );

  useEffect(() => {
    let isMounted = true;

    if (!isAuthenticated || effectiveSearchMode !== 'universal') {
      setActiveMetadataConfig(null);
      return () => {
        isMounted = false;
      };
    }

    const loadMetadataConfig = async () => {
      try {
        const nextConfig = await getMetadataSearchConfig(
          effectiveContentType,
          effectiveMetadataProvider ?? undefined,
        );
        if (isMounted) {
          setActiveMetadataConfig(nextConfig);
        }
      } catch (error) {
        console.error('Failed to load metadata search config:', error);
        if (isMounted) {
          setActiveMetadataConfig(null);
        }
      }
    };

    void loadMetadataConfig();

    return () => {
      isMounted = false;
    };
  }, [isAuthenticated, effectiveSearchMode, effectiveContentType, effectiveMetadataProvider]);

  return activeMetadataConfig;
};
