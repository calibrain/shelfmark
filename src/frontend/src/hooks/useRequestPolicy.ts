import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchRequestPolicy } from '../services/api';
import { ContentType, RequestPolicyMode, RequestPolicyResponse } from '../types';
import {
  DEFAULT_POLICY_TTL_MS,
  RequestPolicyCache,
  resolveDefaultModeFromPolicy,
  resolveSourceModeFromPolicy,
} from './requestPolicyCore';
import { policyTrace } from '../utils/policyTrace';

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

      if (!enabled) {
        cache.reset();
        setPolicy(null);
        setIsLoading(false);
        return null;
      }

      setIsLoading(true);
      try {
        policyTrace('policy.refresh:start', { force, enabled, isAdmin });
        // Always fetch server policy while authenticated so backend auth state
        // remains authoritative even if local auth state is stale.
        const response = await cache.refresh({ enabled, isAdmin: false, force });
        policyTrace('policy.refresh:ok', {
          force,
          requestsEnabled: response?.requests_enabled ?? null,
          defaults: response?.defaults ?? null,
        });
        setPolicy(response);
        return response;
      } catch (error) {
        policyTrace('policy.refresh:error', {
          force,
          message: error instanceof Error ? error.message : String(error),
        });
        throw error;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, isAdmin]
  );

  useEffect(() => {
    if (!enabled) {
      cacheRef.current?.reset();
      setPolicy(null);
      return;
    }
    void fetchPolicy(true);
  }, [enabled, fetchPolicy]);

  const getDefaultMode = useCallback(
    (contentType: ContentType | string): RequestPolicyMode => {
      const effectiveIsAdmin = policy ? Boolean(policy.is_admin) : isAdmin;
      return resolveDefaultModeFromPolicy(policy, effectiveIsAdmin, contentType);
    },
    [policy, isAdmin]
  );

  const getSourceMode = useCallback(
    (source: string, contentType: ContentType | string): RequestPolicyMode => {
      const effectiveIsAdmin = policy ? Boolean(policy.is_admin) : isAdmin;
      return resolveSourceModeFromPolicy(policy, effectiveIsAdmin, source, contentType);
    },
    [policy, isAdmin]
  );

  const refresh = useCallback(async (options: { force?: boolean } = {}) => {
    return fetchPolicy(Boolean(options.force));
  }, [fetchPolicy]);

  return {
    policy,
    isLoading,
    isAdmin: policy ? Boolean(policy.is_admin) : isAdmin,
    requestsEnabled: Boolean(policy?.requests_enabled),
    allowNotes: policy?.allow_notes ?? true,
    getDefaultMode,
    getSourceMode,
    refresh,
  };
}
