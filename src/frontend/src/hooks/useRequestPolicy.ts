import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchRequestPolicy } from '../services/api';
import { ContentType, RequestPolicyMode, RequestPolicyResponse } from '../types';
import {
  DEFAULT_POLICY_TTL_MS,
  RequestPolicyCache,
  resolveDefaultModeFromPolicy,
  resolveSourceModeFromPolicy,
} from './requestPolicyCore';

interface UseRequestPolicyOptions {
  enabled: boolean;
  isAdmin: boolean;
  ttlMs?: number;
}

interface UseRequestPolicyReturn {
  policy: RequestPolicyResponse | null;
  isLoading: boolean;
  isAdmin: boolean;
  requestsEnabled: boolean;
  allowNotes: boolean;
  getDefaultMode: (contentType: ContentType | string) => RequestPolicyMode;
  getSourceMode: (source: string, contentType: ContentType | string) => RequestPolicyMode;
  refresh: (options?: { force?: boolean }) => Promise<RequestPolicyResponse | null>;
}

export function useRequestPolicy({
  enabled,
  isAdmin,
  ttlMs = DEFAULT_POLICY_TTL_MS,
}: UseRequestPolicyOptions): UseRequestPolicyReturn {
  const [policy, setPolicy] = useState<RequestPolicyResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const cacheRef = useRef<RequestPolicyCache | null>(null);

  if (!cacheRef.current) {
    cacheRef.current = new RequestPolicyCache(fetchRequestPolicy, ttlMs);
  }

  useEffect(() => {
    cacheRef.current?.setTtlMs(ttlMs);
  }, [ttlMs]);

  const fetchPolicy = useCallback(
    async (force: boolean): Promise<RequestPolicyResponse | null> => {
      const cache = cacheRef.current;
      if (!cache) {
        return null;
      }

      if (!enabled || isAdmin) {
        cache.reset();
        setPolicy(null);
        setIsLoading(false);
        return null;
      }

      setIsLoading(true);
      try {
        const response = await cache.refresh({ enabled, isAdmin, force });
        setPolicy(response);
        return response;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, isAdmin]
  );

  useEffect(() => {
    if (!enabled || isAdmin) {
      cacheRef.current?.reset();
      setPolicy(null);
      return;
    }
    void fetchPolicy(true);
  }, [enabled, isAdmin, fetchPolicy]);

  const getDefaultMode = useCallback(
    (contentType: ContentType | string): RequestPolicyMode => {
      return resolveDefaultModeFromPolicy(policy, isAdmin, contentType);
    },
    [policy, isAdmin]
  );

  const getSourceMode = useCallback(
    (source: string, contentType: ContentType | string): RequestPolicyMode => {
      return resolveSourceModeFromPolicy(policy, isAdmin, source, contentType);
    },
    [policy, isAdmin]
  );

  const refresh = useCallback(async (options: { force?: boolean } = {}) => {
    return fetchPolicy(Boolean(options.force));
  }, [fetchPolicy]);

  return {
    policy,
    isLoading,
    isAdmin: isAdmin || Boolean(policy?.is_admin),
    requestsEnabled: Boolean(policy?.requests_enabled),
    allowNotes: policy?.allow_notes ?? true,
    getDefaultMode,
    getSourceMode,
    refresh,
  };
}
