import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getSearchByCookie, setSearchByCookie } from '../utils/searchByCookie';

// No jsdom in this project's vitest setup, so stub a minimal document.cookie jar that
// mimics real browser semantics closely enough for get/set round-tripping: assigning a
// "name=value; ..." string upserts that cookie, and reading returns "name=value; ..." pairs.
const makeCookieJar = () => {
  const store = new Map<string, string>();
  return {
    get cookie(): string {
      return Array.from(store.entries())
        .map(([name, value]) => `${name}=${value}`)
        .join('; ');
    },
    set cookie(raw: string) {
      const [pair] = raw.split(';');
      const eqIndex = pair.indexOf('=');
      const name = pair.slice(0, eqIndex).trim();
      const value = pair.slice(eqIndex + 1);
      if (/max-age=0/.test(raw) || value === '') {
        store.delete(name);
      } else {
        store.set(name, value);
      }
    },
  };
};

describe('searchByCookie', () => {
  beforeEach(() => {
    vi.stubGlobal('document', makeCookieJar());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns null when no cookie is set', () => {
    expect(getSearchByCookie()).toBe(null);
  });

  it('round-trips a value through set/get', () => {
    setSearchByCookie('manual');
    expect(getSearchByCookie()).toBe('manual');
  });

  it('overwrites a previously stored value', () => {
    setSearchByCookie('manual');
    setSearchByCookie('author');
    expect(getSearchByCookie()).toBe('author');
  });
});
