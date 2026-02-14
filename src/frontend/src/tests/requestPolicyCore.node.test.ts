import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  RequestPolicyCache,
  resolveDefaultModeFromPolicy,
  resolveSourceModeFromPolicy,
} from '../hooks/requestPolicyCore.js';
import type { RequestPolicyResponse } from '../types/index.js';

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

    assert.equal(resolveDefaultModeFromPolicy(policy, false, 'ebook'), 'request_book');
    assert.equal(resolveDefaultModeFromPolicy(policy, false, 'audiobook'), 'request_release');
    assert.equal(resolveSourceModeFromPolicy(policy, false, 'prowlarr', 'audiobook'), 'blocked');
    assert.equal(resolveSourceModeFromPolicy(policy, false, 'unknown', 'audiobook'), 'request_release');
  });

  it('short-circuits to download for admins and requests-disabled policy', () => {
    const blockedPolicy = makePolicy({
      requests_enabled: false,
      defaults: {
        ebook: 'blocked',
        audiobook: 'blocked',
      },
    });

    assert.equal(resolveDefaultModeFromPolicy(blockedPolicy, false, 'ebook'), 'download');
    assert.equal(resolveSourceModeFromPolicy(blockedPolicy, false, 'prowlarr', 'audiobook'), 'download');
    assert.equal(resolveDefaultModeFromPolicy(makePolicy(), true, 'ebook'), 'download');
    assert.equal(resolveSourceModeFromPolicy(makePolicy(), true, 'prowlarr', 'audiobook'), 'download');
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

    assert.equal(resolveSourceModeFromPolicy(policy, false, 'mystery_source', 'ebook'), 'request_release');
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

    assert.equal(resolveSourceModeFromPolicy(policy, false, 'mystery_source', 'ebook'), 'request_release');
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
      assert.deepEqual(initial, first);
      assert.equal(fetchCount, 1);

      const cached = await cache.refresh({ enabled: true, isAdmin: false });
      assert.deepEqual(cached, first);
      assert.equal(fetchCount, 1);

      now += 60_001;
      const afterTtl = await cache.refresh({ enabled: true, isAdmin: false });
      assert.deepEqual(afterTtl, second);
      assert.equal(fetchCount, 2);

      const forced = await cache.refresh({ enabled: true, isAdmin: false, force: true });
      assert.deepEqual(forced, third);
      assert.equal(fetchCount, 3);
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
    assert.equal(fetchCount, 1);
    assert.equal(pendingResolvers.length, 1);
    const firstResolver = pendingResolvers.shift();
    if (!firstResolver) {
      throw new Error('Missing first in-flight resolver');
    }
    firstResolver(inflightPolicy);

    const [firstResult, secondResult] = await Promise.all([firstPromise, secondPromise]);
    assert.deepEqual(firstResult, inflightPolicy);
    assert.deepEqual(secondResult, inflightPolicy);

    const noAuthResult = await cache.refresh({ enabled: false, isAdmin: false });
    assert.equal(noAuthResult, null);

    const postResetRefresh = cache.refresh({ enabled: true, isAdmin: false, force: true });
    assert.equal(fetchCount, 2);
    const secondResolver = pendingResolvers.shift();
    if (!secondResolver) {
      throw new Error('Missing second in-flight resolver');
    }
    secondResolver(inflightPolicy);
    await postResetRefresh;

    const adminResult = await cache.refresh({ enabled: true, isAdmin: true });
    assert.equal(adminResult, null);
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

    assert.equal(fetchCount, 1);
    const firstResolver = pendingResolvers.shift();
    if (!firstResolver) {
      throw new Error('Missing first in-flight resolver');
    }
    firstResolver();

    const nonForcedResult = await nonForcedPromise;
    assert.deepEqual(nonForcedResult, firstPolicy);
    assert.equal(fetchCount, 2);

    const secondResolver = pendingResolvers.shift();
    if (!secondResolver) {
      throw new Error('Missing second in-flight resolver');
    }
    secondResolver();

    const forcedResult = await forcedPromise;
    assert.deepEqual(forcedResult, secondPolicy);
  });
});
