import { describe, it, expect } from 'vitest';

import { shortBuildId } from '../utils/buildVersion';

const SHA = '1a5b37d0c2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2';

describe('buildVersion.shortBuildId', () => {
  it('shows the commit of a date-stamped image, not the date', () => {
    expect(shortBuildId(`2026-09-20-${SHA}`)).toBe('1a5b37d');
  });

  it('shows the commit of a PR image', () => {
    expect(shortBuildId(`pr-${SHA}`)).toBe('1a5b37d');
  });

  it('shortens a bare commit sha', () => {
    expect(shortBuildId(SHA)).toBe('1a5b37d');
  });

  it('keeps any other stamp to its first seven characters', () => {
    expect(shortBuildId('local-build')).toBe('local-b');
    expect(shortBuildId('abc')).toBe('abc');
  });

  it('returns null for an unstamped build', () => {
    expect(shortBuildId(undefined)).toBe(null);
    expect(shortBuildId('')).toBe(null);
    expect(shortBuildId('N/A')).toBe(null);
  });
});
