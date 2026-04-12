import type { DynamicFieldOption } from '../services/api';
import { fetchFieldOptions } from '../services/api';

const optionsCache = new Map<string, DynamicFieldOption[]>();
const OPTIONS_CACHE_MAX = 50;

const getCachedOptions = (endpoint: string): DynamicFieldOption[] | null => {
  return optionsCache.get(endpoint) ?? null;
};

const cacheOptions = (endpoint: string, options: DynamicFieldOption[]): void => {
  if (optionsCache.size >= OPTIONS_CACHE_MAX) {
    const oldest = optionsCache.keys().next().value;
    if (oldest !== undefined) {
      optionsCache.delete(oldest);
    }
  }
  optionsCache.set(endpoint, options);
};

export const loadDynamicFieldOptions = async (endpoint: string): Promise<DynamicFieldOption[]> => {
  const cached = getCachedOptions(endpoint);
  if (cached) {
    return cached;
  }

  const loaded = await fetchFieldOptions(endpoint);
  cacheOptions(endpoint, loaded);
  return loaded;
};

export const getDynamicOptionGroup = (endpoint: string, value: string): string | undefined => {
  const cached = getCachedOptions(endpoint);
  if (!cached) {
    return undefined;
  }
  return cached.find((option) => option.value === value)?.group;
};
