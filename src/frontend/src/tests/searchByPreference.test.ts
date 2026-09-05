import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getSearchByPreference, setSearchByPreference } from '../utils/searchByPreference';

// No jsdom in this project's vitest setup, so stub the slice of localStorage this uses.
const makeStorage = (impl?: Partial<Storage>) => {
  const store = new Map<string, string>();
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    ...impl,
  };
};

const stubWindow = (storage: unknown) => {
  vi.stubGlobal('window', { localStorage: storage });
};

describe('searchByPreference', () => {
  beforeEach(() => {
    stubWindow(makeStorage());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns null when nothing is stored', () => {
    expect(getSearchByPreference()).toBe(null);
  });

  it('round-trips a value through set/get', () => {
    setSearchByPreference('manual');
    expect(getSearchByPreference()).toBe('manual');
  });

  it('overwrites a previously stored value', () => {
    setSearchByPreference('manual');
    setSearchByPreference('author');
    expect(getSearchByPreference()).toBe('author');
  });

  it('survives storage being unavailable', () => {
    stubWindow(
      makeStorage({
        getItem: () => {
          throw new Error('storage disabled');
        },
        setItem: () => {
          throw new Error('storage disabled');
        },
      }),
    );

    expect(() => setSearchByPreference('author')).not.toThrow();
    expect(getSearchByPreference()).toBe(null);
  });
});
