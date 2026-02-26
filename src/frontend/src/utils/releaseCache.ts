import { ReleasesResponse } from '../types';

// Module-level cache for release search results
// Key format: `${provider}:${provider_id}:${source}:${contentType}`
// This persists across modal open/close cycles
const releaseCache = new Map<string, ReleasesResponse>();

function getCacheKey(provider: string, providerId: string, source: string, contentType: string): string {
  return `${provider}:${providerId}:${source}:${contentType}`;
}

// Default cache TTL (5 minutes) - sources can override via column_config.cache_ttl_seconds
const DEFAULT_CACHE_TTL_MS = 5 * 60 * 1000;
const cacheTimestamps = new Map<string, number>();

export function getCachedReleases(provider: string, providerId: string, source: string, contentType: string): ReleasesResponse | null {
  const key = getCacheKey(provider, providerId, source, contentType);
  const timestamp = cacheTimestamps.get(key);
  const cached = releaseCache.get(key);

  if (!timestamp || !cached) {
    return null;
  }

  // Use source-specific TTL if available, otherwise default
  const ttlSeconds = cached.column_config?.cache_ttl_seconds;
  const ttlMs = ttlSeconds ? ttlSeconds * 1000 : DEFAULT_CACHE_TTL_MS;

  // Check if cache entry is not expired
  if (Date.now() - timestamp < ttlMs) {
    return cached;
  }

  // Clear expired entry
  releaseCache.delete(key);
  cacheTimestamps.delete(key);

  return null;
}

export function setCachedReleases(provider: string, providerId: string, source: string, contentType: string, data: ReleasesResponse): void {
  const key = getCacheKey(provider, providerId, source, contentType);
  releaseCache.set(key, data);
  cacheTimestamps.set(key, Date.now());
}

export function invalidateCachedReleases(provider: string, providerId: string, source: string, contentType: string): void {
  const key = getCacheKey(provider, providerId, source, contentType);
  releaseCache.delete(key);
  cacheTimestamps.delete(key);
}
