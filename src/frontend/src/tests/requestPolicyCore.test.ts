import { describe, it, expect } from 'vitest';

import {
  RequestPolicyCache,
  resolveDefaultModeFromPolicy,
  resolveSourceModeFromPolicy,
} from '../hooks/requestPolicyCore';
import type { RequestPolicyResponse } from '../types/index';

const makePolicy = (overrides: Partial<RequestPolicyResponse> = {}): RequestPolicyResponse => ({
  requests_enabled: true,
  is_admin: false,
  allow_notes: true,
  defaults: {
    ebook: 'download',
    audiobook: 'request_release',
  },
  rules: [],
  source_modes: [
    {
      source: 'direct_download',
      supported_content_types: ['ebook'],
      browse_results_are_releases: true,
      modes: {
        ebook: 'request_release',
      },
    },
    {
      source: 'prowlarr',
      supported_content_types: ['ebook', 'audiobook'],
      modes: {
        ebook: 'download',
        audiobook: 'blocked',
      },
    },
  ],
  ...overrides,
});

describe('requestPolicyCore mode resolution', () => {
  it('resolves default and source modes from policy payload', () => {
    const policy = makePolicy({
      defaults: {
        ebook: 'request_book',
        audiobook: 'request_release',
      },
    });

    expect(resolveDefaultModeFromPolicy(policy, false, 'ebook')).toBe('request_book');
    expect(resolveDefaultModeFromPolicy(policy, false, 'audiobook')).toBe('request_release');
    expect(resolveSourceModeFromPolicy(policy, false, 'prowlarr', 'audiobook')).toBe('blocked');
    expect(resolveSourceModeFromPolicy(policy, false, 'unknown', 'audiobook')).toBe(
      'request_release',
    );
  });

  it('normalizes direct source request_book mode to request_release', () => {
    const policy = makePolicy({
      defaults: {
        ebook: 'request_book',
        audiobook: 'request_release',
      },
      source_modes: [
        {
          source: 'direct_download',
          supported_content_types: ['ebook'],
          browse_results_are_releases: true,
          modes: {},
        },
      ],
      rules: [],
    });

    expect(resolveSourceModeFromPolicy(policy, false, 'direct_download', 'ebook')).toBe(
      'request_release',
    );
  });

  it('short-circuits to download for admins and requests-disabled policy', () => {
    const blockedPolicy = makePolicy({
      requests_enabled: false,
      defaults: {
        ebook: 'blocked',
        audiobook: 'blocked',
      },
    });

    expect(resolveDefaultModeFromPolicy(blockedPolicy, false, 'ebook')).toBe('download');
    expect(resolveSourceModeFromPolicy(blockedPolicy, false, 'prowlarr', 'audiobook')).toBe(
      'download',
    );
    expect(resolveDefaultModeFromPolicy(makePolicy(), true, 'ebook')).toBe('download');
    expect(resolveSourceModeFromPolicy(makePolicy(), true, 'prowlarr', 'audiobook')).toBe(
      'download',
    );
  });

  it('falls back to wildcard rules when source_modes entry is missing', () => {
    const policy = makePolicy({
      defaults: {
        ebook: 'download',
        audiobook: 'request_release',
      },
      source_modes: [],
      rules: [{ source: '*', content_type: 'ebook', mode: 'request_release' }],
    });

    expect(resolveSourceModeFromPolicy(policy, false, 'mystery_source', 'ebook')).toBe(
      'request_release',
    );
  });

  it('caps wildcard rule results to the content default ceiling', () => {
    const policy = makePolicy({
      defaults: {
        ebook: 'request_release',
        audiobook: 'request_release',
      },
      source_modes: [],
      rules: [{ source: '*', content_type: 'ebook', mode: 'download' }],
    });

    expect(resolveSourceModeFromPolicy(policy, false, 'mystery_source', 'ebook')).toBe(
      'request_release',
    );
  });
});

describe('RequestPolicyCache', () => {
  it('uses TTL cache for non-forced refresh and refetches after ttl/force', async () => {
    const originalNow = Date.now;
    let now = 1_000_000;
    Date.now = () => now;
    try {
      const first = makePolicy();
      const second = makePolicy({
        defaults: { ebook: 'request_book', audiobook: 'request_release' },
      });
      const third = makePolicy({
        defaults: { ebook: 'blocked', audiobook: 'blocked' },
      });

      let fetchCount = 0;
      const fetcher = async (): Promise<RequestPolicyResponse> => {
        fetchCount += 1;
        if (fetchCount === 1) return first;
        if (fetchCount === 2) return second;
        return third;
      };

      const cache = new RequestPolicyCache(fetcher, 60_000);

      const initial = await cache.refresh({ enabled: true, isAdmin: false });
      expect(initial).toEqual(first);
      expect(fetchCount).toBe(1);

      const cached = await cache.refresh({ enabled: true, isAdmin: false });
      expect(cached).toEqual(first);
      expect(fetchCount).toBe(1);

      now += 60_001;
      const afterTtl = await cache.refresh({ enabled: true, isAdmin: false });
      expect(afterTtl).toEqual(second);
      expect(fetchCount).toBe(2);

      const forced = await cache.refresh({ enabled: true, isAdmin: false, force: true });
      expect(forced).toEqual(third);
      expect(fetchCount).toBe(3);
    } finally {
      Date.now = originalNow;
    }
  });

  it('deduplicates in-flight refresh calls and resets in no-auth/admin contexts', async () => {
    let fetchCount = 0;
    const pendingResolvers: Array<(value: RequestPolicyResponse) => void> = [];
    const inflightPolicy = makePolicy();

    const fetcher = (): Promise<RequestPolicyResponse> => {
      fetchCount += 1;
      return new Promise<RequestPolicyResponse>((resolve) => {
        pendingResolvers.push(resolve);
      });
    };

    const cache = new RequestPolicyCache(fetcher, 60_000);

    const firstPromise = cache.refresh({ enabled: true, isAdmin: false, force: true });
    const secondPromise = cache.refresh({ enabled: true, isAdmin: false, force: true });
    expect(fetchCount).toBe(1);
    expect(pendingResolvers.length).toBe(1);
    const firstResolver = pendingResolvers.shift();
    if (!firstResolver) {
      throw new Error('Missing first in-flight resolver');
    }
    firstResolver(inflightPolicy);

    const [firstResult, secondResult] = await Promise.all([firstPromise, secondPromise]);
    expect(firstResult).toEqual(inflightPolicy);
    expect(secondResult).toEqual(inflightPolicy);

    const noAuthResult = await cache.refresh({ enabled: false, isAdmin: false });
    expect(noAuthResult).toBe(null);

    const postResetRefresh = cache.refresh({ enabled: true, isAdmin: false, force: true });
    expect(fetchCount).toBe(2);
    const secondResolver = pendingResolvers.shift();
    if (!secondResolver) {
      throw new Error('Missing second in-flight resolver');
    }
    secondResolver(inflightPolicy);
    await postResetRefresh;

    const adminResult = await cache.refresh({ enabled: true, isAdmin: true });
    expect(adminResult).toBe(null);
  });

  it('runs a fresh forced fetch when a non-forced refresh is already in flight', async () => {
    let fetchCount = 0;
    const pendingResolvers: Array<() => void> = [];
    const firstPolicy = makePolicy({
      defaults: { ebook: 'download', audiobook: 'download' },
    });
    const secondPolicy = makePolicy({
      defaults: { ebook: 'request_book', audiobook: 'request_release' },
    });

    const fetcher = (): Promise<RequestPolicyResponse> => {
      fetchCount += 1;
      const response = fetchCount === 1 ? firstPolicy : secondPolicy;
      return new Promise<RequestPolicyResponse>((resolve) => {
        pendingResolvers.push(() => resolve(response));
      });
    };

    const cache = new RequestPolicyCache(fetcher, 60_000);

    const nonForcedPromise = cache.refresh({ enabled: true, isAdmin: false });
    const forcedPromise = cache.refresh({ enabled: true, isAdmin: false, force: true });

    expect(fetchCount).toBe(1);
    const firstResolver = pendingResolvers.shift();
    if (!firstResolver) {
      throw new Error('Missing first in-flight resolver');
    }
    firstResolver();

    const nonForcedResult = await nonForcedPromise;
    expect(nonForcedResult).toEqual(firstPolicy);
    expect(fetchCount).toBe(2);

    const secondResolver = pendingResolvers.shift();
    if (!secondResolver) {
      throw new Error('Missing second in-flight resolver');
    }
    secondResolver();

    const forcedResult = await forcedPromise;
    expect(forcedResult).toEqual(secondPolicy);
  });
});
