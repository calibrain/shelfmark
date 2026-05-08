import { describe, it, expect } from 'vitest';

import type { Language } from '../types/index';
import {
  LANGUAGE_OPTION_ALL,
  LANGUAGE_OPTION_DEFAULT,
  buildLanguageNormalizer,
  getReleaseSearchLanguageParams,
  releaseLanguageMatchesFilter,
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

  it('normalizes legacy default language names when combined with explicit filters', () => {
    const result = getReleaseSearchLanguageParams(
      [LANGUAGE_OPTION_DEFAULT, 'de'],
      supportedLanguages,
      ['english'],
    );

    expect(result).toEqual(['en', 'de']);
  });
});

describe('releaseLanguageMatchesFilter', () => {
  it('matches release language names against legacy default language names', () => {
    const normalizer = buildLanguageNormalizer(supportedLanguages);

    expect(releaseLanguageMatchesFilter('English', ['english'], normalizer)).toBe(true);
  });

  it('keeps English-only issue 948 fallback results with a legacy English default', () => {
    const normalizer = buildLanguageNormalizer(supportedLanguages);
    const issue948Languages = [...Array<string>(48).fill('en'), 'de, en', 'en, es'];

    const visibleLanguages = issue948Languages.filter((language) =>
      releaseLanguageMatchesFilter(language, ['english'], normalizer),
    );

    expect(visibleLanguages).toHaveLength(48);
  });
});
