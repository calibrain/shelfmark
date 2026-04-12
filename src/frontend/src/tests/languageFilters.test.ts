import { describe, it, expect } from 'vitest';

import type { Language } from '../types/index';
import {
  LANGUAGE_OPTION_ALL,
  LANGUAGE_OPTION_DEFAULT,
  getReleaseSearchLanguageParams,
} from '../utils/languageFilters';

const supportedLanguages: Language[] = [
  { code: 'en', language: 'English' },
  { code: 'de', language: 'German' },
  { code: 'hu', language: 'Hungarian' },
];

describe('languageFilters release search params', () => {
  it('omits languages when only default selection is active', () => {
    const result = getReleaseSearchLanguageParams([LANGUAGE_OPTION_DEFAULT], supportedLanguages, [
      'en',
    ]);

    expect(result).toBe(undefined);
  });

  it('preserves explicit all-languages selection', () => {
    const result = getReleaseSearchLanguageParams([LANGUAGE_OPTION_ALL], supportedLanguages, [
      'en',
    ]);

    expect(result).toEqual([LANGUAGE_OPTION_ALL]);
  });

  it('resolves explicit language selections to codes', () => {
    const result = getReleaseSearchLanguageParams(['de', 'hu'], supportedLanguages, ['en']);

    expect(result).toEqual(['de', 'hu']);
  });
});
